from sqlalchemy import select
from src.config import INVENTORY_POLL_INTERVAL
from src.db.models import InventoryItem, Tenant
from src.db.session import SessionLocal
from src.events.bus import publish_event
from src.schemas.event import EventCategory, InventoryEventType
from src.schemas.inventory import ThresholdCrossedPayload
import time


def check_thresholds(tenant_id: str) -> None:
    with SessionLocal() as session:
        items = session.scalars(
            select(InventoryItem).where(
                InventoryItem.quantity_on_hand <= InventoryItem.reorder_point,
                InventoryItem.tenant_id == tenant_id,
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
                tenant_id=tenant_id,
            )


def inventory_checker() -> None:
    while True:
        with SessionLocal() as session:
            tenants = session.scalars(select(Tenant)).all()
        for tenant in tenants:
            check_thresholds(str(tenant.id))
        time.sleep(INVENTORY_POLL_INTERVAL)

if __name__ == "__main__":
    inventory_checker()