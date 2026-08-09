import uuid
from collections import defaultdict
from decimal import Decimal
from math import ceil

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.models import (
    CapabilityState,
    DecisionLog,
    InventoryItem,
    POEvent,
    POLine,
    PurchaseOrder,
    Supplier,
    SupplierItem,
    Tenant,
)
from src.events.bus import publish_event
from src.schemas.event import EventCategory, ProcurementEventType
from src.schemas.learning import FactorKind
from src.schemas.orders import OrderBy, PredictionMode
from src.schemas.suppliers import POStatus
from src.services.config_services import resolve_config
from src.services.learning_service import get_factor
from src.services.ordering_service import (
    autonomy_checks,
    horizon_aggregate,
    protection_horizon,
)


def generate_proposals(
    session: Session, tenant_id, item_ids: list[str]
) -> list[PurchaseOrder]:
    po_placed = session.execute(
        select(POLine, PurchaseOrder)
        .join(PurchaseOrder, POLine.purchase_order_id == PurchaseOrder.id)
        .where(PurchaseOrder.status.in_([POStatus.PROPOSED, POStatus.APPROVED]))
        .where(POLine.tenant_id == tenant_id)
        .where(PurchaseOrder.created_by == OrderBy.SYSTEM.value)
    ).all()

    existing_proposed = []
    po_approved_inv_ids = []

    for line, po in po_placed:
        if po.status == POStatus.APPROVED:
            po_approved_inv_ids.append(line.inventory_item_id)
        else:
            existing_proposed.append((line, po))

    existing_pos: dict[uuid.UUID, PurchaseOrder] = {}
    existing_lines: dict[uuid.UUID, POLine] = {}

    for line, po in existing_proposed:
        existing_pos[po.supplier_id] = po
        existing_lines[line.inventory_item_id] = line

    low_items = session.scalars(
        select(InventoryItem)
        .where(InventoryItem.tenant_id == tenant_id)
        .where(InventoryItem.id.in_(item_ids))
        .where(InventoryItem.quantity_on_hand <= InventoryItem.reorder_point)
        .where(InventoryItem.id.not_in(po_approved_inv_ids))
    ).all()

    if not low_items:
        return []

    low_item_ids = [item.id for item in low_items]
    item_map = {item.id: item for item in low_items}

    sent_orders = session.execute(
        select(
            POLine.inventory_item_id,
            func.coalesce(func.sum(POLine.quantity_ordered), 0).label("total_quantity"),
        )
        .join(PurchaseOrder, POLine.purchase_order_id == PurchaseOrder.id)
        .where(POLine.tenant_id == tenant_id)
        .where(PurchaseOrder.status.in_([POStatus.SENT, POStatus.CONFIRMED]))
        .group_by(POLine.inventory_item_id)
    ).all()

    sent_orders_map = {item_id: total for item_id, total in sent_orders}

    all_supplier_items = session.scalars(
        select(SupplierItem)
        .join(Supplier, Supplier.id == SupplierItem.supplier_id)
        .where(SupplierItem.tenant_id == tenant_id)
        .where(SupplierItem.inventory_item_id.in_(low_item_ids))
        .where(Supplier.is_active == True)
    ).all()

    supplier_ids = {si.supplier_id for si in all_supplier_items}
    suppliers = session.scalars(
        select(Supplier)
        .where(Supplier.id.in_(supplier_ids))
        .where(Supplier.tenant_id == tenant_id)
    ).all()

    supplier_map = {s.id: s for s in suppliers}
    supplier_items = [
        si
        for si in all_supplier_items
        if not supplier_map[si.supplier_id].delivery_days
    ]

    if not supplier_items:
        return []

    best_by_item: dict[uuid.UUID, SupplierItem] = {}
    for si in supplier_items:
        existing = best_by_item.get(si.inventory_item_id)
        if (
            existing is None
            or supplier_map[si.supplier_id].lead_time_days
            < supplier_map[existing.supplier_id].lead_time_days
        ):
            best_by_item[si.inventory_item_id] = si

    supplier_groups = defaultdict(list)
    for si in best_by_item.values():
        supplier_groups[si.supplier_id].append(si)

    processed_pos = []

    timezone = session.scalar(select(Tenant.timezone).where(Tenant.id == tenant_id))
    events_to_publish = []

    for supplier_id, items in supplier_groups.items():
        state = session.scalar(
            select(CapabilityState.state)
            .where(CapabilityState.tenant_id == tenant_id)
            .where(CapabilityState.supplier_id == supplier_id)
        )

        config = resolve_config(tenant_id, session)

        po = existing_pos.get(supplier_id)

        supplier = supplier_map[supplier_id]

        if not supplier or not timezone:
            continue

        dates = protection_horizon(
            supplier.lead_time_days, supplier.order_cutoff_hours, timezone
        )

        if not dates:
            continue

        start_date, end_date = dates

        if po is None:
            po = PurchaseOrder(
                tenant_id=tenant_id,
                supplier_id=supplier_id,
                status=POStatus.PROPOSED.value,
                total_value=Decimal(0),
                created_by=OrderBy.SYSTEM.value,
            )
            session.add(po)
            session.flush()

        for si in items:
            inv_item = item_map[si.inventory_item_id]
            existing_line = existing_lines.get(si.inventory_item_id)
            position = inv_item.quantity_on_hand + sent_orders_map.get(
                inv_item.id, Decimal(0)
            )
            shortfall = inv_item.target_quantity - position
            mode = PredictionMode.PAR.value
            aggregate_pe = None
            aggregate_qg = None
            qk = None

            aggregates = horizon_aggregate(
                session, str(inv_item.id), tenant_id, start_date, end_date
            )

            if aggregates:
                qk = f"p{int(config.ordering.default_service_level * 100)}"
                aggregate_pe, aggregate_qg = aggregates
                s = aggregate_qg[qk]
                shortfall = s - position
                mode = PredictionMode.FORECAST.value

            edit_bias = Decimal(get_factor(session, tenant_id, FactorKind.ORDER_EDIT_BIAS, str(si.id)))
            shortfall *= edit_bias
            
            quantity_ordered = max(
                0,
                ceil((shortfall) / si.pack_size) * si.pack_size,
            )
            unit_cost = si.cost_per_unit

            if quantity_ordered == 0:
                continue

            snapshot = {
                "mode": mode,
                "quantity_on_hand": float(inv_item.quantity_on_hand),
                "on_order": float(sent_orders_map.get(inv_item.id, Decimal(0))),
                "position": float(position),
                "shortfall": float(shortfall),
                "quantity_ordered": float(quantity_ordered),
                "pack_size": float(si.pack_size),
                "unit_cost": float(unit_cost),
                "protection_horizon": {
                    "start": start_date.isoformat(),
                    "end": end_date.isoformat(),
                },
                "quantile_key": qk,
                "aggregate_demand": float(aggregate_pe)
                if aggregate_pe is not None
                else None,
                "quantile_grid": {k: float(v) for k, v in aggregate_qg.items()}
                if aggregate_qg
                else None,
                "target_quantity": float(inv_item.target_quantity)
                if qk is None
                else None,
            }

            session.add(
                DecisionLog(
                    tenant_id=tenant_id,
                    purchase_order_id=po.id,
                    inventory_item_id=si.inventory_item_id,
                    supplier_id=si.supplier_id,
                    snapshot=snapshot,
                )
            )

            if existing_line:
                existing_line.quantity_ordered = quantity_ordered
                existing_line.unit_cost = unit_cost
            else:
                po_line = POLine(
                    tenant_id=tenant_id,
                    purchase_order_id=po.id,
                    inventory_item_id=si.inventory_item_id,
                    supplier_item_id=si.id,
                    quantity_ordered=quantity_ordered,
                    unit_cost=unit_cost,
                )
                session.add(po_line)
                session.flush()

        is_new = supplier_id not in existing_pos

        session.add(
            POEvent(
                tenant_id=tenant_id,
                purchase_order_id=po.id,
                from_status=None if is_new else POStatus.PROPOSED.value,
                to_status=POStatus.PROPOSED.value,
                changed_by=OrderBy.SYSTEM.value,
                note="Auto-generated proposal"
                if is_new
                else "Proposal updated with current quantities",
            )
        )

        all_lines = session.scalars(
            select(POLine)
            .where(POLine.purchase_order_id == po.id)
            .where(POLine.tenant_id == tenant_id)
        ).all()
        po.total_value = sum(l.quantity_ordered * l.unit_cost for l in all_lines)

        supplier = supplier_map[supplier_id]
        if (
            supplier.minimum_order_value
            and po.total_value < supplier.minimum_order_value
        ):
            current_inv_ids = [si.inventory_item_id for si in items]

            candidates = session.execute(
                select(SupplierItem, InventoryItem)
                .join(InventoryItem, SupplierItem.inventory_item_id == InventoryItem.id)
                .where(SupplierItem.supplier_id == supplier_id)
                .where(SupplierItem.tenant_id == tenant_id)
                .where(SupplierItem.inventory_item_id.not_in(current_inv_ids))
            ).all()

            candidates.sort(key=lambda row: row[1].quantity_on_hand)

            po.suggested_topups = [
                {
                    "inventory_item_id": str(inv.id),
                    "supplier_item_id": str(si.id),
                    "name": inv.name,
                    "quantity_on_hand": float(inv.quantity_on_hand),
                    "target_quantity": float(inv.target_quantity),
                    "unit": inv.unit,
                    "pack_size": float(si.pack_size),
                    "cost_per_unit": float(si.cost_per_unit),
                }
                for si, inv in candidates
            ]
        else:
            po.suggested_topups = None

        all_passed = autonomy_checks(session, timezone, tenant_id, po, supplier, state)

        if all_passed:
            po.status = POStatus.APPROVED.value
            session.add(
                POEvent(
                    tenant_id=tenant_id,
                    purchase_order_id=po.id,
                    from_status=POStatus.PROPOSED.value,
                    to_status=POStatus.APPROVED.value,
                    changed_by=OrderBy.SYSTEM.value,
                    note="Auto-approved within autonomy bounds",
                )
            )

            events_to_publish.append(
                {
                    "purchase_order_id": str(po.id),
                    "tenant_id": str(tenant_id),
                    "changed_by": OrderBy.SYSTEM.value,
                }
            )

        processed_pos.append(po)

    session.commit()
    for evt in events_to_publish:
        publish_event(
            EventCategory.PROCUREMENT,
            ProcurementEventType.PO_APPROVED.value,
            "2",
            {"purchase_order_id": evt["purchase_order_id"]},
            evt["tenant_id"],
        )
    return processed_pos
