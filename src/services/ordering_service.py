import uuid
from collections import defaultdict
from decimal import Decimal
from math import ceil

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models.inventory import InventoryItem
from src.db.models.suppliers import (
    POEvent,
    POLine,
    PurchaseOrder,
    Supplier,
    SupplierItem,
)
from src.schemas.orders import OrderBy
from src.schemas.suppliers import POStatus


def generate_proposals(session: Session, tenant_id, item_ids: list[str]) -> list[PurchaseOrder]:
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

    supplier_items = session.scalars(
        select(SupplierItem)
        .join(Supplier, Supplier.id == SupplierItem.supplier_id)
        .where(SupplierItem.tenant_id == tenant_id)
        .where(SupplierItem.inventory_item_id.in_(low_item_ids))
        .where(Supplier.is_active == True)
    ).all()

    supplier_ids = {si.supplier_id for si in supplier_items}
    suppliers = session.scalars(
        select(Supplier)
        .where(Supplier.id.in_(supplier_ids))
        .where(Supplier.tenant_id == tenant_id)
    ).all()
    supplier_map = {s.id: s for s in suppliers}

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
    for supplier_id, items in supplier_groups.items():
        po = existing_pos.get(supplier_id)

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

            quantity_ordered = max(
                0,
                ceil(
                    (inv_item.target_quantity - inv_item.quantity_on_hand)
                    / si.pack_size
                )
                * si.pack_size,
            )
            unit_cost = si.cost_per_unit
            
            if quantity_ordered == 0:
                continue

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
            select(POLine).where(POLine.purchase_order_id == po.id)
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
        
        processed_pos.append(po)

    session.commit()
    return processed_pos