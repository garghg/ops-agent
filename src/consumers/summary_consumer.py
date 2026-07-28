import time
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from decimal import Decimal
from zoneinfo import ZoneInfo

import redis
from jinja2 import Environment, FileSystemLoader
from sqlalchemy import func, select

from src.config import CLAIM_INTERVAL_SECONDS
from src.consumers.utils import CONSUMER_NAME
from src.db.models import (
    EmailOutbox,
    InventoryItem,
    PurchaseOrder,
    SaleTransaction,
    Supplier,
    Tenant,
)
from src.db.session import SessionLocal
from src.events.bus import claim_pending_events, r, read_event
from src.logging import get_logger, setup_logging
from src.schemas.email import EmailStatus
from src.schemas.event import ConsumerGroup, EventCategory, SystemEventType
from src.schemas.sale import SaleTransactionType
from src.schemas.suppliers import POStatus
from src.services.health_service import check_heartbeats, record_heartbeat

SYSTEM_STREAM = f"{EventCategory.SYSTEM.value}_events"
env = Environment(loader=FileSystemLoader("src/templates"))
template = env.get_template("daily_summary.html")
log = get_logger(__name__)

def process_events(events: list[dict]):
    for event in events:
        try:
            if event["event_type"] != SystemEventType.DAY_CLOSED.value:
                r.xack(SYSTEM_STREAM, ConsumerGroup.SUMMARY_CONSUMER.value, event["id"])
                continue
            business_date = event["payload"]["business_date"]
            tenant_id = event["tenant_id"]
        except Exception as e:  # noqa: BLE001
            print(f"Bad payload, dropping event {event['id']}: {e}")
            r.xack(SYSTEM_STREAM, ConsumerGroup.SUMMARY_CONSUMER.value, event["id"])
            continue

        try:
            with SessionLocal() as session:
                tenant = session.scalar(select(Tenant).where(Tenant.id == tenant_id))

                if not tenant.owner_email:
                    log.warning("no_owner_email", tenant_id=str(tenant_id))
                    r.xack(SYSTEM_STREAM, ConsumerGroup.SUMMARY_CONSUMER.value, event["id"])
                    continue

                business_date = date.fromisoformat(business_date)
                tz = ZoneInfo(tenant.timezone)
                day_start = datetime.combine(business_date, dt_time.min, tzinfo=tz)
                day_end = datetime.combine(
                    business_date + timedelta(days=1), dt_time.min, tzinfo=tz
                )

                sales_summary = session.execute(
                    select(
                        func.count().label("transaction_count"),
                        func.coalesce(
                            func.sum(SaleTransaction.total), Decimal(0)
                        ).label("revenue"),
                        func.coalesce(
                            func.sum(SaleTransaction.discount_amount), Decimal(0)
                        ).label("total_discounts"),
                        func.count()
                        .filter(
                            SaleTransaction.transaction_type == SaleTransactionType.VOID
                        )
                        .label("void_count"),
                        func.count()
                        .filter(
                            SaleTransaction.transaction_type
                            == SaleTransactionType.REFUND
                        )
                        .label("refund_count"),
                    ).where(
                        SaleTransaction.tenant_id == tenant_id,
                        SaleTransaction.timestamp >= day_start,
                        SaleTransaction.timestamp < day_end,
                    )
                ).first()
                
                low_inventory = session.scalars(
                    select(InventoryItem)
                    .where(InventoryItem.tenant_id == tenant_id)
                    .where(InventoryItem.quantity_on_hand < InventoryItem.reorder_point)
                ).all()
                
                pending_proposals = session.execute(
                    select(PurchaseOrder, Supplier)
                    .join(Supplier, PurchaseOrder.supplier_id == Supplier.id)
                    .where(PurchaseOrder.status == POStatus.PROPOSED.value)
                    .where(PurchaseOrder.tenant_id == tenant_id)
                ).all()
                
                stale = check_heartbeats(session)

                html = template.render(
                    tenant=tenant,
                    business_date=str(business_date),
                    sales=sales_summary,
                    low_inventory=low_inventory,
                    pending_proposals=pending_proposals,
                    stale_consumers=stale
                )
                
                session.add(EmailOutbox(
                    tenant_id=tenant_id,
                    idempotency_key=f"summary-{tenant_id}-{business_date}",
                    recipient=tenant.owner_email,
                    subject=f"Daily Summary for {tenant.name}",
                    body_html=html,
                    status=EmailStatus.PENDING.value,
                ))
                session.commit()
                
        except Exception as e:  # noqa: BLE001
            print(f"Error processing event {event['id']}: {e}")
            r.xack(SYSTEM_STREAM, ConsumerGroup.SUMMARY_CONSUMER.value, event["id"])
            continue

        r.xack(SYSTEM_STREAM, ConsumerGroup.SUMMARY_CONSUMER.value, event["id"])


def summary_consumer():
    last_claim_check = 0.0

    while True:
        try:
            events = read_event(
                EventCategory.SYSTEM,
                ConsumerGroup.SUMMARY_CONSUMER.value,
                CONSUMER_NAME,
            )
        except redis.exceptions.TimeoutError:
            events = []

        process_events(events)

        now = time.monotonic()
        if now - last_claim_check >= CLAIM_INTERVAL_SECONDS:
            last_claim_check = now
            claimed_events = claim_pending_events(
                EventCategory.SYSTEM,
                ConsumerGroup.SUMMARY_CONSUMER.value,
                CONSUMER_NAME,
            )
            process_events(claimed_events)

        with SessionLocal() as session:
            record_heartbeat(session, "summary_consumer")


if __name__ == "__main__":
    setup_logging()
    summary_consumer()
