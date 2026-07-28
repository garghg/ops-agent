import time

import redis
from sqlalchemy.exc import IntegrityError

from src.config import CLAIM_INTERVAL_SECONDS
from src.consumers.utils import CONSUMER_NAME
from src.db.models import SaleLineItem, SaleTransaction
from src.db.session import SessionLocal
from src.events.bus import claim_pending_events, r, read_event
from src.schemas.event import ConsumerGroup, EventCategory
from src.schemas.sale import SaleEvent
from src.services.health_service import record_heartbeat

SALES_STREAM = f"{EventCategory.SALES.value}_events"

def process_events(events: list[dict]):
    for event in events:
        try:
            sales = SaleEvent(**event["payload"])
            tenant_id = event["tenant_id"]
        except Exception as e:  # noqa: BLE001
                    print(f"Bad payload, dropping event {event['id']}: {e}")
                    r.xack(SALES_STREAM, ConsumerGroup.SALES_CONSUMER.value, event["id"])
                    continue
        
        try:
            with SessionLocal() as session:
                
                transaction = SaleTransaction(
                    external_transaction_id=sales.external_transaction_id,
                    source=sales.source,
                    timestamp=sales.timestamp,
                    total=sales.total,
                    payment_method=sales.payment_method,
                    tenant_id=tenant_id
                )
                
                session.add(transaction)
                session.flush()
                
                for sale in sales.line_items:
                    session.add(SaleLineItem(
                        sale_transaction_id=transaction.id,
                        item_name=sale.item_name,
                        modifiers=sale.modifiers,
                        quantity=sale.quantity,
                        unit_price=sale.unit_price,
                        tenant_id=tenant_id
                    ))
                
                session.commit()
                
        except IntegrityError as e:
            if "sale_transactions_tenant_id_external_transaction_id_key" in str(e.orig):
                print(f"Event {event['id']} already processed, skipping.")
            else:
                raise
            
        r.xack(SALES_STREAM, ConsumerGroup.SALES_CONSUMER.value, event["id"])

def sales_consumer():
    last_claim_check = 0.0
    
    while True:
        try:
            events = read_event(
                EventCategory.SALES,
                ConsumerGroup.SALES_CONSUMER.value,
                CONSUMER_NAME
            )
        except redis.exceptions.TimeoutError:
            events = []
        
        process_events(events)
        
        now = time.monotonic()
        if now - last_claim_check >= CLAIM_INTERVAL_SECONDS:
            last_claim_check = now
            claimed_events = claim_pending_events(
                EventCategory.SALES,
                ConsumerGroup.SALES_CONSUMER.value,
                CONSUMER_NAME,
            )
            process_events(claimed_events)
            
        with SessionLocal() as session:
            record_heartbeat(session, "sales_consumer")


if __name__ == "__main__":
    sales_consumer()
