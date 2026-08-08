import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import redis
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import select

from src.config import CLAIM_INTERVAL_SECONDS
from src.consumers.utils import CONSUMER_NAME
from src.db.models import (
    AutonomyEvent,
    CapabilityState,
    EmailOutbox,
    InventoryItem,
    POEvent,
    POLine,
    PurchaseOrder,
    SpendLedger,
    Supplier,
    Tenant,
)
from src.db.session import SessionLocal
from src.events.bus import claim_pending_events, r, read_event
from src.logging import get_logger, setup_logging
from src.schemas.autonomy import AutonomyEventType, AutonomyState
from src.schemas.email import EmailStatus
from src.schemas.event import ConsumerGroup, EventCategory, ProcurementEventType
from src.schemas.orders import OrderBy
from src.schemas.suppliers import POStatus
from src.services.health_service import record_heartbeat
from src.services.ordering_service import autonomy_checks

EMAIL_STREAM = f"{EventCategory.PROCUREMENT.value}_events"
log = get_logger(__name__)
env = Environment(loader=FileSystemLoader("src/templates"))
template = env.get_template("supplier_order.html")


def process_events(events: list[dict]) -> None:
    for event in events:
        if event["event_type"] != ProcurementEventType.PO_APPROVED.value:
            r.xack(EMAIL_STREAM, ConsumerGroup.EMAIL_CONSUMER.value, event["id"])
            continue
        try:
            data = event["payload"]
            po_id = data["purchase_order_id"]
            tenant_id = event["tenant_id"]
        except Exception as e:  # noqa: BLE001
            print(f"Bad payload, dropping event {event['id']}: {e}")
            r.xack(EMAIL_STREAM, ConsumerGroup.EMAIL_CONSUMER.value, event["id"])
            continue

        try:
            with SessionLocal() as session:
                changed_by = data["changed_by"]

                timezone = session.scalar(
                    select(Tenant.timezone).where(Tenant.id == tenant_id)
                )

                result = session.execute(
                    select(PurchaseOrder, Supplier)
                    .join(Supplier, PurchaseOrder.supplier_id == Supplier.id)
                    .where(PurchaseOrder.id == po_id)
                    .where(PurchaseOrder.tenant_id == tenant_id)
                ).first()

                po, supplier = result

                state = session.scalar(
                    select(CapabilityState.state)
                    .where(CapabilityState.tenant_id == tenant_id)
                    .where(CapabilityState.supplier_id == supplier.id)
                )

                if changed_by == OrderBy.SYSTEM.value and not autonomy_checks(
                    session, timezone, tenant_id, po, supplier, state
                ):
                    po.status = POStatus.PROPOSED.value
                    session.add(
                        POEvent(
                            tenant_id=tenant_id,
                            purchase_order_id=po.id,
                            from_status=POStatus.APPROVED.value,
                            to_status=POStatus.PROPOSED.value,
                            changed_by=OrderBy.SYSTEM.value,
                            note="Executor rejected: autonomy bounds failed re-check",
                        )
                    )
                    session.add(
                        AutonomyEvent(
                            tenant_id=tenant_id,
                            supplier_id=supplier.id,
                            event_type=AutonomyEventType.EXECUTOR_REJECTED.value,
                            from_state=AutonomyState.AUTO_WITHIN_BOUNDS.value,
                            to_state=AutonomyState.AUTO_WITHIN_BOUNDS.value,
                            reason="Autonomy bounds failed re-check at executor",
                        )
                    )
                    log.warning(
                        "executor_rejected_auto_approval",
                        purchase_order_id=str(po.id),
                        supplier_id=str(supplier.id),
                        tenant_id=str(tenant_id),
                    )
                    session.commit()
                    r.xack(
                        EMAIL_STREAM, ConsumerGroup.EMAIL_CONSUMER.value, event["id"]
                    )
                    continue

                po_lines = session.execute(
                    select(POLine, InventoryItem)
                    .join(InventoryItem, POLine.inventory_item_id == InventoryItem.id)
                    .where(POLine.purchase_order_id == po.id)
                    .where(POLine.tenant_id == tenant_id)
                ).all()

                tenant = session.scalar(select(Tenant).where(Tenant.id == tenant_id))

                html = template.render(
                    supplier_name=supplier.name,
                    tenant=tenant,
                    lines=[
                        {
                            "item_name": item.name,
                            "quantity": line.quantity_ordered,
                        }
                        for line, item in po_lines
                    ],
                )

                session.add(
                    EmailOutbox(
                        tenant_id=tenant_id,
                        idempotency_key=f"po-order-{po.id}",
                        recipient=supplier.email,
                        subject=f"Purchase Order from {tenant.name}",
                        body_html=html,
                        status=EmailStatus.PENDING.value,
                        purchase_order_id=po.id,
                    )
                )

                if changed_by == OrderBy.SYSTEM.value:
                    today = datetime.now(ZoneInfo(timezone)).date()
                    session.add(
                        SpendLedger(
                            tenant_id=tenant_id,
                            purchase_order_id=po.id,
                            amount=po.total_value,
                            business_date=today,
                        )
                    )

                session.commit()

        except Exception as e:  # noqa: BLE001
            print(f"Error processing event {event['id']}: {e}")
            r.xack(EMAIL_STREAM, ConsumerGroup.EMAIL_CONSUMER.value, event["id"])
            continue

        r.xack(EMAIL_STREAM, ConsumerGroup.EMAIL_CONSUMER.value, event["id"])


def email_consumer():
    last_claim_check = 0.0

    while True:
        try:
            events = read_event(
                EventCategory.PROCUREMENT,
                ConsumerGroup.EMAIL_CONSUMER.value,
                CONSUMER_NAME,
            )
        except redis.exceptions.TimeoutError:
            events = []

        process_events(events)

        now = time.monotonic()
        if now - last_claim_check >= CLAIM_INTERVAL_SECONDS:
            last_claim_check = now
            claimed_events = claim_pending_events(
                EventCategory.PROCUREMENT,
                ConsumerGroup.EMAIL_CONSUMER.value,
                CONSUMER_NAME,
            )
            process_events(claimed_events)

        with SessionLocal() as session:
            record_heartbeat(session, "email_consumer")


if __name__ == "__main__":
    setup_logging()
    email_consumer()
