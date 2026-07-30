import time

import redis

from src.config import CLAIM_INTERVAL_SECONDS
from src.consumers.utils import CONSUMER_NAME
from src.db.session import SessionLocal
from src.events.bus import claim_pending_events, publish_event, r, read_event
from src.logging import get_logger, setup_logging
from src.schemas.event import ConsumerGroup, EventCategory, SystemEventType
from src.services.forecast_service import (
    actuals_aggregate,
    compute_forecast_metrics,
    forecast_glm,
    forecast_seasonal_naive,
    forecast_trailing_mean,
)
from src.services.health_service import record_heartbeat

SYSTEM_STREAM = f"{EventCategory.SYSTEM.value}_events"
log = get_logger(__name__)


def process_events(events: list[dict]):
    for event in events:
        try:
            if event["event_type"] != SystemEventType.DAY_CLOSED.value:
                r.xack(SYSTEM_STREAM, ConsumerGroup.FORECAST_CONSUMER.value, event["id"])
                continue
            business_date = event["payload"]["business_date"]
            tenant_id = event["tenant_id"]
        except Exception as e:  # noqa: BLE001
            print(f"Bad payload, dropping event {event['id']}: {e}")
            r.xack(SYSTEM_STREAM, ConsumerGroup.FORECAST_CONSUMER.value, event["id"])
            continue

        try:
            with SessionLocal() as session:
                actuals_aggregate(session, tenant_id, business_date)
                forecast_seasonal_naive(session, tenant_id, business_date)
                forecast_trailing_mean(session, tenant_id, business_date)
                forecast_glm(session, tenant_id, business_date)
                compute_forecast_metrics(session, tenant_id, business_date)

            publish_event(
                EventCategory.SYSTEM,
                SystemEventType.FORECASTS_COMPUTED.value,
                tenant_id,
                {"business_date": business_date},
            )

        except Exception as e:  # noqa: BLE001
            log.error("forecast_processing_failed", tenant_id=str(tenant_id), error=str(e))
            r.xack(SYSTEM_STREAM, ConsumerGroup.FORECAST_CONSUMER.value, event["id"])
            continue

        r.xack(SYSTEM_STREAM, ConsumerGroup.FORECAST_CONSUMER.value, event["id"])


def forecast_consumer():
    last_claim_check = 0.0

    while True:
        try:
            events = read_event(
                EventCategory.SYSTEM,
                ConsumerGroup.FORECAST_CONSUMER.value,
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
                ConsumerGroup.FORECAST_CONSUMER.value,
                CONSUMER_NAME,
            )
            process_events(claimed_events)

        with SessionLocal() as session:
            record_heartbeat(session, "forecast_consumer")


if __name__ == "__main__":
    setup_logging()
    forecast_consumer()