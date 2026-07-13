from sqlalchemy import select
from src.config import INVENTORY_POLL_INTERVAL
from src.db.models import InventoryItem
from src.db.session import SessionLocal
from src.events.bus import publish_event
from src.schemas.event import EventCategory, InventoryEventType
from src.schemas.inventory import ThresholdCrossedPayload
import time

def check_thresholds() -> None:
    with SessionLocal() as session:
        items = session.scalars(
            select(InventoryItem).where(
                InventoryItem.quantity_on_hand <= InventoryItem.reorder_point
            )
        )

        for item in items:
            payload = ThresholdCrossedPayload(
                item_id=item.id,
                quantity_on_hand=item.quantity_on_hand,
                reorder_point=item.reorder_point,
                reorder_quantity=item.reorder_quantity,
            )
            publish_event(
                EventCategory.INVENTORY,
                InventoryEventType.BELOW_REORDER_POINT.value,
                "2",
                payload.model_dump(mode="json"),
            )

def inventory_checker() -> None:
    while True:
        check_thresholds()
        time.sleep(INVENTORY_POLL_INTERVAL)
   
if __name__ == "__main__":
    inventory_checker()