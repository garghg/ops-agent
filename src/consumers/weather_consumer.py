import time

import redis

from src.config import CLAIM_INTERVAL_SECONDS
from src.consumers.utils import CONSUMER_NAME
from src.db.session import SessionLocal
from src.events.bus import claim_pending_events, r, read_event
from src.schemas.event import ConsumerGroup, EventCategory, SystemEventType
from src.services.health_service import record_heartbeat
from src.services.weather_service import collect_weather

SYSTEM_STREAM = f"{EventCategory.SYSTEM.value}_events"


def listen_event(events: list[dict]) -> None:
    for event in events:
        try:
            if event["event_type"] == SystemEventType.DAY_OPENED.value:
                business_date = event["payload"]["business_date"]
                collect_weather(business_date)
        except Exception as e:  # noqa: BLE001
            print(f"Bad payload, dropping event {event['id']}: {e}")
            
        r.xack(SYSTEM_STREAM, ConsumerGroup.WEATHER_CONSUMER.value, event["id"])


def weather_consumer():
    last_claim_check = 0.0
    while True:
        try:
            events = read_event(
                EventCategory.SYSTEM,
                ConsumerGroup.WEATHER_CONSUMER.value,
                CONSUMER_NAME,
            )
        except redis.exceptions.TimeoutError:
            events = []

        listen_event(events)

        now = time.monotonic()
        if now - last_claim_check >= CLAIM_INTERVAL_SECONDS:
            last_claim_check = now
            claimed = claim_pending_events(
                EventCategory.SYSTEM,
                ConsumerGroup.WEATHER_CONSUMER.value,
                CONSUMER_NAME,
            )
            listen_event(claimed)
            
        with SessionLocal() as session:
            record_heartbeat(session, "weather_consumer")
