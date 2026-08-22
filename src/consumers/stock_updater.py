import time
from decimal import Decimal

import redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from src.clock import get_now
from src.config import CLAIM_INTERVAL_SECONDS
from src.consumers.utils import CONSUMER_NAME
from src.db.models import InventoryItem, InventoryTransaction
from src.db.session import SessionLocal
from src.events.bus import claim_pending_events, r, read_event
from src.schemas.anomaly import AnomalySubject, AnomalyType
from src.schemas.event import ConsumerGroup, EventCategory
from src.schemas.inventory import (
    SUBTRACT_TYPES,
    InventoryEventPayload,
    InventoryTransactionType,
)
from src.services.anomaly_service import persist_anomaly
from src.services.config_services import resolve_config
from src.services.health_service import record_heartbeat

INVENTORY_STREAM = f"{EventCategory.INVENTORY.value}_events"


def process_events(events: list[dict]) -> None:
    for event in events:
        try:
            payload = InventoryEventPayload(**event["payload"])
            tenant_id = event["tenant_id"]
        except Exception as e:  # noqa: BLE001
            print(f"Bad payload, dropping event {event['id']}: {e}")
            r.xack(INVENTORY_STREAM, ConsumerGroup.STOCK_UPDATER.value, event["id"])
            continue

        try:
            with SessionLocal() as session:
                config = resolve_config(tenant_id, session)
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
                    if (
                        payload.transaction_type == InventoryTransactionType.SHRINKAGE
                        and item.quantity_on_hand - magnitude < 0
                    ):
                        capped = max(Decimal(0), item.quantity_on_hand)
                        if capped <= 0:
                            r.xack(
                                INVENTORY_STREAM,
                                ConsumerGroup.STOCK_UPDATER.value,
                                event["id"],
                            )
                            continue
                        payload.quantity = capped
                        item.quantity_on_hand -= capped
                    else:
                        item.quantity_on_hand -= magnitude
                        if item.quantity_on_hand < 0:
                            persist_anomaly(
                                session,
                                tenant_id,
                                AnomalyType.INVENTORY_UNDERFLOW,
                                f"{AnomalySubject.NEGATIVE_STOCK}:{item.id}",
                                1,
                                get_now().date(),
                                {
                                    "inventory": float(item.quantity_on_hand),
                                    "transaction": float(magnitude),
                                },
                                f"{item.name} inventory went negative ({float(item.quantity_on_hand)}) after depleting {float(magnitude)} units. Possible missed restock or count error. Please recount.",
                                config.anomalies.cooldown_hours,
                            )
                else:
                    item.quantity_on_hand += magnitude

                session.add(
                    InventoryTransaction(
                        item_id=payload.item_id,
                        quantity_change=payload.quantity,
                        transaction_type=payload.transaction_type,
                        note=payload.note,
                        event_id=payload.source_key
                        if payload.source_key
                        else event["id"],
                        tenant_id=tenant_id,
                        occurred_at=get_now(),
                    )
                )

                session.commit()
            
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
        except redis.exceptions.TimeoutError:  # type: ignore
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

        with SessionLocal() as session:
            record_heartbeat(session, "stock_updater")


if __name__ == "__main__":
    stock_updater()
