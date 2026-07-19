import os
import json
import redis
from dotenv import load_dotenv
from src.schemas.event import EventCategory

load_dotenv()

r = redis.Redis(
    host=os.environ["REDIS_HOST"],
    port=int(os.environ["REDIS_PORT"]),
    decode_responses=True,
    socket_timeout=10,
)


def publish_event(
    category: EventCategory, event_type: str, priority: str, payload: dict, tenant_id: str
) -> str:
    stream = f"{category.value}_events"
    return r.xadd(
        stream,
        {
            "event_type": event_type,
            "tenant_id": tenant_id,
            "priority": priority,
            "payload": json.dumps(payload),
        },
    )


def create_group(category: EventCategory, group_name: str) -> None:
    stream = f"{category.value}_events"
    try:
        r.xgroup_create(stream, group_name, id="$", mkstream=True)
    except redis.exceptions.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


def _to_event_dict(message_id: str, fields: dict) -> dict:
    return {
        "id": message_id,
        "event_type": fields["event_type"],
        "tenant_id": fields["tenant_id"],
        "priority": fields["priority"],
        "payload": json.loads(fields["payload"]),
    }


def read_event(
    category: EventCategory,
    group_name: str,
    consumer_name: str,
    count: int = 10,
    block_ms: int = 5000,
) -> list[dict]:
    stream = f"{category.value}_events"

    create_group(category, group_name)

    response = r.xreadgroup(
        groupname=group_name,
        consumername=consumer_name,
        streams={stream: ">"},
        count=count,
        block=block_ms,
    )

    if not response:
        return []

    events = []
    for _, messages in response:
        for message_id, fields in messages:
            events.append(_to_event_dict(message_id, fields))

    return events


def claim_pending_events(
    category: EventCategory,
    group_name: str,
    consumer_name: str,
    min_idle_ms: int = 60000,
    count: int = 100,
) -> list[dict]:
    stream = f"{category.value}_events"

    claimed: list[dict] = []
    start_id = "0-0"

    while True:
        next_start_id, claimed_messages, deleted_messages = r.xautoclaim(
            stream,
            group_name,
            consumer_name,
            min_idle_ms,
            start_id=start_id,
            count=count,
        )

        for message_id, fields in claimed_messages:
            claimed.append(_to_event_dict(message_id, fields))


        if deleted_messages:
            r.xack(stream, group_name, *deleted_messages)

        if next_start_id == "0-0":
            break
        start_id = next_start_id

    return claimed