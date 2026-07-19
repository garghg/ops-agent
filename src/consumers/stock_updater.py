import time
import redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from src.config import CLAIM_INTERVAL_SECONDS
from src.events.bus import claim_pending_events, read_event, r
from src.db.session import SessionLocal
from src.db.models import InventoryItem, InventoryTransaction
from src.schemas.inventory import InventoryEventPayload, InventoryTransactionType
from src.schemas.event import ConsumerGroup, EventCategory
from src.consumers.utils import CONSUMER_NAME

SUBTRACT_TYPES = {
    InventoryTransactionType.USAGE,
    InventoryTransactionType.WASTE,
    InventoryTransactionType.ADJUSTMENT_SUB,
}

INVENTORY_STREAM = f"{EventCategory.INVENTORY.value}_events"


def process_events(events: list[dict]) -> None:
    for event in events:
        try:
            payload = InventoryEventPayload(**event["payload"])
            tenant_id = event["tenant_id"]
        except Exception as e:
            print(f"Bad payload, dropping event {event['id']}: {e}")
            r.xack(INVENTORY_STREAM, ConsumerGroup.STOCK_UPDATER.value, event["id"])
            continue

        try:
            with SessionLocal() as session:
                with session.begin():
                    item = session.scalar(
                        select(InventoryItem).where(
                            InventoryItem.id == payload.item_id,
                            InventoryItem.tenant_id == tenant_id,
                        )
                    )
                    if item is None:
                        raise ValueError(f"item_id {payload.item_id} not found")

                    magnitude = abs(payload.quantity)
                    if payload.transaction_type in SUBTRACT_TYPES:
                        item.quantity_on_hand -= magnitude
                    else:
                        item.quantity_on_hand += magnitude

                    session.add(
                        InventoryTransaction(
                            item_id=payload.item_id,
                            quantity_change=payload.quantity,
                            transaction_type=payload.transaction_type,
                            note=payload.note,
                            event_id=event["id"],
                            tenant_id=tenant_id
                        )
                    )
        except ValueError as e:
            print(f"Skipping event {event['id']}: {e}")
            r.xack(INVENTORY_STREAM, ConsumerGroup.STOCK_UPDATER.value, event["id"])
            continue
        except IntegrityError as e:
            if "inventory_transactions_tenant_id_event_id_key" in str(e.orig):
                print(f"Event {event['id']} already processed, skipping.")
            else:
                raise

        r.xack(INVENTORY_STREAM, ConsumerGroup.STOCK_UPDATER.value, event["id"])


def stock_updater() -> None:
    last_claim_check = 0.0

    while True:
        try:
            events = read_event(
                EventCategory.INVENTORY,
                ConsumerGroup.STOCK_UPDATER.value,
                CONSUMER_NAME,
            )
        except redis.exceptions.TimeoutError:
            events = []

        process_events(events)

        now = time.monotonic()
        if now - last_claim_check >= CLAIM_INTERVAL_SECONDS:
            last_claim_check = now
            claimed_events = claim_pending_events(
                EventCategory.INVENTORY,
                ConsumerGroup.STOCK_UPDATER.value,
                CONSUMER_NAME,
            )
            process_events(claimed_events)


if __name__ == "__main__":
    stock_updater()
