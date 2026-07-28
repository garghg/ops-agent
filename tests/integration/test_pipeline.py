from datetime import date
from decimal import Decimal
from scripts.synthetic_generator import generate_sales
from src.events.bus import read_event, create_group
from src.consumers.bom_consumer import process_events as bom_process
from src.consumers.stock_updater import process_events as stock_process
from src.schemas.event import EventCategory, ConsumerGroup
from src.db.models import InventoryItem, InventoryTransaction


def test_full_pipeline(seeded_db):
    create_group(EventCategory.SALES, ConsumerGroup.BOM_CONSUMER.value)
    create_group(EventCategory.INVENTORY, ConsumerGroup.STOCK_UPDATER.value)

    generate_sales(date(2026, 7, 22), date(2026, 7, 22))

    while True:
        events = read_event(EventCategory.SALES, ConsumerGroup.BOM_CONSUMER.value, "test", count=100, block_ms=500)
        if not events:
            break
        bom_process(events)

    while True:
        events = read_event(EventCategory.INVENTORY, ConsumerGroup.STOCK_UPDATER.value, "test", count=100, block_ms=500)
        if not events:
            break
        stock_process(events)

    seeded_db.expire_all()

    spoons = seeded_db.query(InventoryItem).filter_by(name="Plastic Spoons").first()
    assert spoons.quantity_on_hand < Decimal("400.00"), "Spoons should have been depleted"

    vanilla = seeded_db.query(InventoryItem).filter_by(name="Vanilla Bean Ice Cream").first()
    chocolate = seeded_db.query(InventoryItem).filter_by(name="Chocolate Ice Cream").first()
    strawberry = seeded_db.query(InventoryItem).filter_by(name="Strawberry Ice Cream").first()
    total_ice_cream = vanilla.quantity_on_hand + chocolate.quantity_on_hand + strawberry.quantity_on_hand
    assert total_ice_cream < Decimal("36.00"), "Total ice cream should have decreased"

    tx_count = seeded_db.query(InventoryTransaction).count()
    assert tx_count > 0, "Should have created inventory transactions"