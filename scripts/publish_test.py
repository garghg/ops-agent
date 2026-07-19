from src.events.bus import publish_event
from src.schemas.event import EventCategory

publish_event(
    category=EventCategory.INVENTORY,
    event_type="inventory_transaction",
    priority="normal",
    payload={
        "item_id": "4faa36fa-280c-437b-bb11-f4034d2a3ca6",
        "quantity": "2.5",
        "transaction_type": "usage",
        "note": "test event",
    },
)
print("published")