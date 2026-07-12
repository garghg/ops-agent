from decimal import Decimal
from src.db.session import SessionLocal
from src.db.models import InventoryItem

with SessionLocal() as session:
    with session.begin():
        item = InventoryItem(
            name="Vanilla Base",
            quantity_on_hand=Decimal("50.0"),
            reorder_point=Decimal("10.0"),
            reorder_quantity=Decimal("40.0"),
            cost_per_unit=Decimal("3.50"),
            supplier="Test Supplier",
        )
        session.add(item)
    session.refresh(item)
    print(f"Inserted item id: {item.id}")