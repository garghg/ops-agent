import time

from sqlalchemy import select

from src.config import INVENTORY_POLL_INTERVAL
from src.db.models import InventoryItem, Tenant
from src.db.session import SessionLocal
from src.events.bus import publish_event
from src.schemas.event import EventCategory, InventoryEventType


def check_thresholds(tenant_id: str) -> None:
    with SessionLocal() as session:
        items = session.scalars(
            select(InventoryItem).where(
                InventoryItem.quantity_on_hand <= InventoryItem.reorder_point,
                InventoryItem.tenant_id == tenant_id,
            )
        ).all()

        if not items:
            return

        payload = {
            "item_ids": [str(item.id) for item in items],
        }

        publish_event(
            EventCategory.INVENTORY,
            InventoryEventType.BELOW_REORDER_POINT.value,
            "2",
            payload,
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
