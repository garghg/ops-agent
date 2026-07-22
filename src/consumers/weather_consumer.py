import redis
import time
from src.consumers.utils import CONSUMER_NAME
from src.events.bus import read_event, r, claim_pending_events
from src.schemas.event import ConsumerGroup, EventCategory, SystemEventType
from src.services.weather_service import collect_weather
from src.config import CLAIM_INTERVAL_SECONDS

SYSTEM_STREAM = f"{EventCategory.SYSTEM.value}_events"


def listen_event(events: list[dict]) -> None:
    for event in events:
        if event["event_type"] == SystemEventType.DAY_OPENED.value:
            collect_weather()
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
