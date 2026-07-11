import redis
import json

r = redis.Redis(host="localhost", port=6379, decode_responses=True)

def publish_event(category, event_type, priority, payload):
    return r.xadd(
        f"{category}_events",
        {
            "event_type": event_type,
            "priority": priority,
            "payload": json.dumps(payload),
        },
    )


def create_group(category, group_name):
    stream = f"{category}_events"
    try:
        r.xgroup_create(stream, group_name, id="$", mkstream=True)
    except redis.exceptions.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


def read_event(category, group_name, consumer_name, count=10, block_ms=5000):
    stream = f"{category}_events"

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
            events.append({
                "id": message_id,
                "event_type": fields["event_type"],
                "priority": fields["priority"],
                "payload": json.loads(fields["payload"]),
            })

    return events