import time
from datetime import date

import redis

from src.config import CLAIM_INTERVAL_SECONDS
from src.consumers.utils import CONSUMER_NAME
from src.db.session import SessionLocal
from src.events.bus import claim_pending_events, publish_event, r, read_event
from src.logging import get_logger, setup_logging
from src.schemas.event import ConsumerGroup, EventCategory, SystemEventType
from src.services.anomaly_service import run_day_close_checks, run_intraday_check
from src.services.health_service import record_heartbeat

SYSTEM_STREAM = f"{EventCategory.SYSTEM.value}_events"
log = get_logger(__name__)

def process_events(events: list[dict]):
    for event in events:
        try:
            if event["event_type"] not in [
                SystemEventType.FORECASTS_COMPUTED.value,
                SystemEventType.INTRADAY_CHECKPOINT.value,
            ]:
                r.xack(SYSTEM_STREAM, ConsumerGroup.ANOMALY_CONSUMER.value, event["id"])
                continue
            event_type = event["event_type"]
            business_date = event["payload"]["business_date"]
            tenant_id = event["tenant_id"]
        except Exception as e:  # noqa: BLE001
            print(f"Bad payload, dropping event {event['id']}: {e}")
            r.xack(SYSTEM_STREAM, ConsumerGroup.ANOMALY_CONSUMER.value, event["id"])
            continue

        try:
            with SessionLocal() as session:
                bd = date.fromisoformat(business_date)
                if event_type == SystemEventType.INTRADAY_CHECKPOINT.value:
                    run_intraday_check(session, tenant_id, bd)

                if event_type == SystemEventType.FORECASTS_COMPUTED.value:
                    run_day_close_checks(session, tenant_id, bd)
                    publish_event(
                        EventCategory.SYSTEM,
                        SystemEventType.ANOMALIES_PROCESSED.value,
                        "4",
                        {"business_date": business_date},
                        tenant_id,
                    )
                    
        except Exception as e:  # noqa: BLE001
            log.error(
                "anomalies_processing_failed", tenant_id=str(tenant_id), error=str(e)
            )
            r.xack(SYSTEM_STREAM, ConsumerGroup.ANOMALY_CONSUMER.value, event["id"])
            continue

        r.xack(SYSTEM_STREAM, ConsumerGroup.ANOMALY_CONSUMER.value, event["id"])


def anomaly_consumer():
    last_claim_check = 0.0

    while True:
        try:
            events = read_event(
                EventCategory.SYSTEM,
                ConsumerGroup.ANOMALY_CONSUMER.value,
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
                ConsumerGroup.ANOMALY_CONSUMER.value,
                CONSUMER_NAME,
            )
            process_events(claimed_events)

        with SessionLocal() as session:
            record_heartbeat(session, "anomaly_consumer")


if __name__ == "__main__":
    setup_logging()
    anomaly_consumer()