import random
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import exists, func, select

from schemas.anomaly import AnomalyAction
from src.db.models import (
    Anomaly,
    AnomalyFeedback,
    POEvent,
    POLine,
    PurchaseOrder,
    Supplier,
    SupplierItem,
)
from src.db.session import SessionLocal
from src.events.bus import publish_event
from src.schemas.event import EventCategory, InventoryEventType
from src.schemas.inventory import InventoryTransactionType
from src.schemas.learning import FactorKind
from src.schemas.orders import OrderBy
from src.schemas.suppliers import POStatus
from src.services.config_services import resolve_config
from src.services.learning_service import update_factor
from src.services.ordering_service.autonomy_metrics import evaluate_demotion


def handle_proposals(tenant_id: str, business_date: date):

    with SessionLocal() as session, session.begin():
        proposed = session.scalars(
            select(PurchaseOrder)
            .where(PurchaseOrder.tenant_id == tenant_id)
            .where(PurchaseOrder.status == POStatus.PROPOSED.value)
        ).all()

        if not proposed:
            return

        for proposal in proposed:
            chance = random.uniform(0, 1)

            if chance > 0.95:
                proposal.status = POStatus.CANCELLED
                session.add(
                    POEvent(
                        tenant_id=tenant_id,
                        purchase_order_id=proposal.id,
                        from_status=POStatus.PROPOSED.value,
                        to_status=POStatus.CANCELLED.value,
                        changed_by=OrderBy.OWNER.value,
                    )
                )
                evaluate_demotion(session, tenant_id, str(proposal.supplier_id))
                continue

            if 0.75 < chance <= 0.95:
                limit = random.randint(1, 2)
                results = session.execute(
                    select(POLine, SupplierItem)
                    .join(SupplierItem, POLine.supplier_item_id == SupplierItem.id)
                    .where(POLine.tenant_id == tenant_id)
                    .where(POLine.purchase_order_id == proposal.id)
                    .order_by(func.random())
                    .limit(limit)
                ).all()

                if not results:
                    continue

                config = resolve_config(tenant_id, session)

                for line, si in results:
                    edit_from = line.quantity_ordered
                    edit_magnitude = random.uniform(0.10, 0.25)
                    add_sub = random.randint(1, 2)
                    edit_amount = line.quantity_ordered * Decimal(
                        str(round(edit_magnitude, 2))
                    )
                    edit_amount = max(
                        si.pack_size, round(edit_amount / si.pack_size) * si.pack_size
                    )

                    if add_sub == 1 and line.quantity_ordered - edit_amount > 0:
                        line.quantity_ordered -= edit_amount
                        proposal.total_value -= edit_amount * line.unit_cost
                    else:
                        line.quantity_ordered += edit_amount
                        proposal.total_value += edit_amount * line.unit_cost

                    edit_to = line.quantity_ordered
                    observation = edit_to / edit_from
                    update_factor(
                        session,
                        tenant_id,
                        FactorKind.ORDER_EDIT_BIAS,
                        str(line.supplier_item_id),
                        float(observation),
                        config.learning.order_edit_half_life,
                        config.learning.order_edit_clamp_low,
                        config.learning.order_edit_clamp_high,
                        business_date,
                    )

            proposal.status = POStatus.APPROVED
            session.add(
                POEvent(
                    tenant_id=tenant_id,
                    purchase_order_id=proposal.id,
                    from_status=POStatus.PROPOSED.value,
                    to_status=POStatus.APPROVED.value,
                    changed_by=OrderBy.OWNER.value,
                )
            )
            proposal.status = POStatus.SENT
            proposal.ordered_at = datetime.combine(
                business_date, datetime.min.time(), tzinfo=UTC
            )
            session.add(
                POEvent(
                    tenant_id=tenant_id,
                    purchase_order_id=proposal.id,
                    from_status=POStatus.APPROVED.value,
                    to_status=POStatus.SENT.value,
                    changed_by=OrderBy.SYSTEM.value,
                )
            )
            lead_days = session.scalar(
                select(Supplier.lead_time_days).where(
                    Supplier.id == proposal.supplier_id
                )
            )
            if not lead_days:
                continue
            proposal.expected_delivery = business_date + timedelta(days=lead_days)
            proposal.status = POStatus.CONFIRMED
            session.add(
                POEvent(
                    tenant_id=tenant_id,
                    purchase_order_id=proposal.id,
                    from_status=POStatus.SENT.value,
                    to_status=POStatus.CONFIRMED.value,
                    changed_by=OrderBy.OWNER.value,
                )
            )


def handle_deliveries(tenant_id: str, business_date: date):
    with SessionLocal() as session, session.begin():
        overdue = session.scalars(
            select(PurchaseOrder)
            .where(PurchaseOrder.tenant_id == tenant_id)
            .where(PurchaseOrder.status == POStatus.CONFIRMED.value)
            .where(PurchaseOrder.expected_delivery < business_date)
        ).all()

        # Due today — 75% receive, 25% become overdue tomorrow
        due_today = session.scalars(
            select(PurchaseOrder)
            .where(PurchaseOrder.tenant_id == tenant_id)
            .where(PurchaseOrder.status == POStatus.CONFIRMED.value)
            .where(PurchaseOrder.expected_delivery == business_date)
        ).all()

        # Due tomorrow — 5% early delivery
        due_tomorrow = session.scalars(
            select(PurchaseOrder)
            .where(PurchaseOrder.tenant_id == tenant_id)
            .where(PurchaseOrder.status == POStatus.CONFIRMED.value)
            .where(PurchaseOrder.expected_delivery == business_date + timedelta(days=1))
        ).all()

        to_receive = list(overdue)
        to_receive += [po for po in due_today if random.random() < 0.75]
        to_receive += [po for po in due_tomorrow if random.random() < 0.05]

        for po in to_receive:
            po.status = POStatus.RECEIVED
            po.actual_delivery = business_date
            session.add(
                POEvent(
                    tenant_id=tenant_id,
                    purchase_order_id=po.id,
                    from_status=POStatus.CONFIRMED.value,
                    to_status=POStatus.RECEIVED.value,
                    changed_by=OrderBy.OWNER.value,
                )
            )

            po_lines = session.scalars(
                select(POLine)
                .where(POLine.purchase_order_id == po.id)
                .where(POLine.tenant_id == tenant_id)
            ).all()

            short_delivery = random.random() < 0.10

            for line in po_lines:
                qty = line.quantity_ordered
                if short_delivery:
                    qty = round(qty * Decimal(str(random.uniform(0.85, 0.95))))
                    qty = max(qty, 1)

                publish_event(
                    EventCategory.INVENTORY,
                    InventoryEventType.ORDER_RECEIVED.value,
                    "2",
                    {
                        "item_id": str(line.inventory_item_id),
                        "quantity": float(qty),
                        "transaction_type": InventoryTransactionType.RESTOCK.value,
                        "note": f"PO {po.id} received (sim)",
                    },
                    tenant_id,
                )


def handle_anomalies(tenant_id: str):
    with SessionLocal() as session, session.begin():
        anomalies = session.scalars(
            select(Anomaly)
            .where(Anomaly.tenant_id == tenant_id)
            .where(
                ~exists(
                    select(AnomalyFeedback.id).where(
                        AnomalyFeedback.anomaly_id == Anomaly.id
                    )
                )
            )
        ).all()

        if not anomalies:
            return

        for anomaly in anomalies:
            if random.random() < 0.75:
                session.add(
                    AnomalyFeedback(
                        tenant_id=tenant_id,
                        anomaly_id=anomaly.id,
                        action=AnomalyAction.ACK,
                    )
                )
            else:
                session.add(
                    AnomalyFeedback(
                        tenant_id=tenant_id,
                        anomaly_id=anomaly.id,
                        action=AnomalyAction.DISMISS,
                    )
                )

