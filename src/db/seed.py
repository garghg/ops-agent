from decimal import Decimal

from sqlalchemy import text

from src.db.models import InventoryItem, Tenant
from src.db.session import SessionLocal
from src.services.tenant import create_tenant, get_system_timezone
from src.schemas.tenant import ShopType

SEED_ITEMS = [
    dict(
        name="Vanilla Bean Ice Cream Base",
        quantity_on_hand=Decimal("18.00"),
        reorder_point=Decimal("10.00"),
        reorder_quantity=Decimal("20.00"),
        cost_per_unit=Decimal("4.25"),
        supplier="Dairy Farms Co-op",
        shelf_life_days=21,
    ),
    dict(
        name="Chocolate Ice Cream Base",
        quantity_on_hand=Decimal("6.00"),
        reorder_point=Decimal("10.00"),
        reorder_quantity=Decimal("20.00"),
        cost_per_unit=Decimal("4.50"),
        supplier="Dairy Farms Co-op",
        shelf_life_days=21,
    ),
    dict(
        name="Strawberry Ice Cream Base",
        quantity_on_hand=Decimal("12.00"),
        reorder_point=Decimal("8.00"),
        reorder_quantity=Decimal("15.00"),
        cost_per_unit=Decimal("4.75"),
        supplier="Dairy Farms Co-op",
        shelf_life_days=14,
    ),
    dict(
        name="Waffle Cones",
        quantity_on_hand=Decimal("240.00"),
        reorder_point=Decimal("100.00"),
        reorder_quantity=Decimal("300.00"),
        cost_per_unit=Decimal("0.35"),
        supplier="Sunrise Bakery Supply",
        shelf_life_days=90,
    ),
    dict(
        name="Cake Cones",
        quantity_on_hand=Decimal("50.00"),
        reorder_point=Decimal("100.00"),
        reorder_quantity=Decimal("300.00"),
        cost_per_unit=Decimal("0.15"),
        supplier="Sunrise Bakery Supply",
        shelf_life_days=120,
    ),
    dict(
        name="Rainbow Sprinkles",
        quantity_on_hand=Decimal("4.50"),
        reorder_point=Decimal("2.00"),
        reorder_quantity=Decimal("5.00"),
        cost_per_unit=Decimal("6.00"),
        supplier="Sweet Toppings Inc",
        shelf_life_days=365,
    ),
    dict(
        name="Hot Fudge Sauce",
        quantity_on_hand=Decimal("3.00"),
        reorder_point=Decimal("4.00"),
        reorder_quantity=Decimal("10.00"),
        cost_per_unit=Decimal("8.50"),
        supplier="Sweet Toppings Inc",
        shelf_life_days=180,
    ),
    dict(
        name="Caramel Sauce",
        quantity_on_hand=Decimal("7.00"),
        reorder_point=Decimal("4.00"),
        reorder_quantity=Decimal("10.00"),
        cost_per_unit=Decimal("7.75"),
        supplier="Sweet Toppings Inc",
        shelf_life_days=180,
    ),
    dict(
        name="Whipped Cream",
        quantity_on_hand=Decimal("9.00"),
        reorder_point=Decimal("6.00"),
        reorder_quantity=Decimal("12.00"),
        cost_per_unit=Decimal("5.20"),
        supplier="Dairy Farms Co-op",
        shelf_life_days=10,
    ),
    dict(
        name="Maraschino Cherries",
        quantity_on_hand=Decimal("2.00"),
        reorder_point=Decimal("3.00"),
        reorder_quantity=Decimal("6.00"),
        cost_per_unit=Decimal("9.00"),
        supplier="Sweet Toppings Inc",
        shelf_life_days=270,
    ),
    dict(
        name="16oz Paper Cups",
        quantity_on_hand=Decimal("180.00"),
        reorder_point=Decimal("150.00"),
        reorder_quantity=Decimal("500.00"),
        cost_per_unit=Decimal("0.08"),
        supplier="Sunrise Bakery Supply",
        shelf_life_days=None,
    ),
    dict(
        name="Plastic Spoons",
        quantity_on_hand=Decimal("400.00"),
        reorder_point=Decimal("200.00"),
        reorder_quantity=Decimal("1000.00"),
        cost_per_unit=Decimal("0.02"),
        supplier="Sunrise Bakery Supply",
        shelf_life_days=None,
    ),
]


def seed() -> None:
    with SessionLocal() as session:
        with session.begin():
            session.execute(
                text(
                    "TRUNCATE TABLE inventory_transactions, inventory_items, tenants "
                    "RESTART IDENTITY CASCADE"
                )
            )

            tenant = create_tenant(
                name="Dev Shop",
                location="Vancouver",
                shop_type=ShopType.ICE_CREAM,
                session=session,
            )
            session.flush()

            session.add_all(
                InventoryItem(**item, tenant_id=tenant.id) for item in SEED_ITEMS
            )

    print(f"Seeded 1 tenant and {len(SEED_ITEMS)} inventory items.")


if __name__ == "__main__":
    seed()