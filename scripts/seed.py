from decimal import Decimal

from sqlalchemy import text

from src.schemas.template import TemplateConfig
from src.services.tenant_service import create_tenant
from src.db.models import (
    InventoryItem,
    Template,
    CatalogItem,
    CatalogModifier,
    BOMLine,
)
from src.db.session import SessionLocal
from src.schemas.tenant import ShopType
from src.logging import setup_logging, get_logger

log = get_logger("seed")

# --- Inventory items (what gets consumed) ---

SEED_ITEMS = [
    dict(
        name="Vanilla Bean Ice Cream",
        quantity_on_hand=Decimal("18.00"),
        reorder_point=Decimal("10.00"),
        reorder_quantity=Decimal("20.00"),
        cost_per_unit=Decimal("4.25"),
        supplier="Dairy Farms Co-op",
        shelf_life_days=21,
        unit="kg",
    ),
    dict(
        name="Chocolate Ice Cream",
        quantity_on_hand=Decimal("6.00"),
        reorder_point=Decimal("10.00"),
        reorder_quantity=Decimal("20.00"),
        cost_per_unit=Decimal("4.50"),
        supplier="Dairy Farms Co-op",
        shelf_life_days=21,
        unit="kg",
    ),
    dict(
        name="Strawberry Ice Cream",
        quantity_on_hand=Decimal("12.00"),
        reorder_point=Decimal("8.00"),
        reorder_quantity=Decimal("15.00"),
        cost_per_unit=Decimal("4.75"),
        supplier="Dairy Farms Co-op",
        shelf_life_days=14,
        unit="kg",
    ),
    dict(
        name="Waffle Cones",
        quantity_on_hand=Decimal("240.00"),
        reorder_point=Decimal("100.00"),
        reorder_quantity=Decimal("300.00"),
        cost_per_unit=Decimal("0.35"),
        supplier="Sunrise Bakery Supply",
        shelf_life_days=90,
        unit="count",
    ),
    dict(
        name="Cake Cones",
        quantity_on_hand=Decimal("50.00"),
        reorder_point=Decimal("100.00"),
        reorder_quantity=Decimal("300.00"),
        cost_per_unit=Decimal("0.15"),
        supplier="Sunrise Bakery Supply",
        shelf_life_days=120,
        unit="count",
    ),
    dict(
        name="Rainbow Sprinkles",
        quantity_on_hand=Decimal("4.50"),
        reorder_point=Decimal("2.00"),
        reorder_quantity=Decimal("5.00"),
        cost_per_unit=Decimal("6.00"),
        supplier="Sweet Toppings Inc",
        shelf_life_days=365,
        unit="kg",
    ),
    dict(
        name="Hot Fudge Sauce",
        quantity_on_hand=Decimal("3.00"),
        reorder_point=Decimal("4.00"),
        reorder_quantity=Decimal("10.00"),
        cost_per_unit=Decimal("8.50"),
        supplier="Sweet Toppings Inc",
        shelf_life_days=180,
        unit="kg",
    ),
    dict(
        name="Caramel Sauce",
        quantity_on_hand=Decimal("7.00"),
        reorder_point=Decimal("4.00"),
        reorder_quantity=Decimal("10.00"),
        cost_per_unit=Decimal("7.75"),
        supplier="Sweet Toppings Inc",
        shelf_life_days=180,
        unit="kg",
    ),
    dict(
        name="Whipped Cream",
        quantity_on_hand=Decimal("9.00"),
        reorder_point=Decimal("6.00"),
        reorder_quantity=Decimal("12.00"),
        cost_per_unit=Decimal("5.20"),
        supplier="Dairy Farms Co-op",
        shelf_life_days=10,
        unit="kg",
    ),
    dict(
        name="Maraschino Cherries",
        quantity_on_hand=Decimal("200.00"),
        reorder_point=Decimal("100.00"),
        reorder_quantity=Decimal("400.00"),
        cost_per_unit=Decimal("0.05"),
        supplier="Sweet Toppings Inc",
        shelf_life_days=270,
        unit="count",
    ),
    dict(
        name="Paper Cups",
        quantity_on_hand=Decimal("180.00"),
        reorder_point=Decimal("150.00"),
        reorder_quantity=Decimal("500.00"),
        cost_per_unit=Decimal("0.08"),
        supplier="Sunrise Bakery Supply",
        shelf_life_days=None,
        unit="count",
    ),
    dict(
        name="Plastic Spoons",
        quantity_on_hand=Decimal("400.00"),
        reorder_point=Decimal("200.00"),
        reorder_quantity=Decimal("1000.00"),
        cost_per_unit=Decimal("0.02"),
        supplier="Sunrise Bakery Supply",
        shelf_life_days=None,
        unit="count",
    ),
    dict(
        name="Bowls",
        quantity_on_hand=Decimal("150.00"),
        reorder_point=Decimal("100.00"),
        reorder_quantity=Decimal("300.00"),
        cost_per_unit=Decimal("0.10"),
        supplier="Sunrise Bakery Supply",
        shelf_life_days=None,
        unit="count",
    ),
]

# --- Catalog items (what customers buy) ---

CATALOG_ITEMS = [
    dict(name="Single Scoop", category="scoops", sale_price=Decimal("4.50")),
    dict(name="Double Scoop", category="scoops", sale_price=Decimal("6.50")),
    dict(name="Sundae", category="sundaes", sale_price=Decimal("8.00")),
]

# --- Catalog modifiers (applied at the register) ---

CATALOG_MODIFIERS = [
    dict(name="Vanilla", category="flavor"),
    dict(name="Chocolate", category="flavor"),
    dict(name="Strawberry", category="flavor"),
    dict(name="Waffle Cone", category="cone"),
    dict(name="Cake Cone", category="cone"),
    dict(name="Rainbow Sprinkles", category="topping"),
    dict(name="Hot Fudge", category="topping"),
    dict(name="Caramel", category="topping"),
    dict(name="Whipped Cream", category="topping"),
    dict(name="Cherry", category="topping"),
]

# --- BOM generation rules ---

# Rule 1: which inventory item does each modifier deplete?
MODIFIER_INVENTORY_MAP = {
    "Vanilla": "Vanilla Bean Ice Cream",
    "Chocolate": "Chocolate Ice Cream",
    "Strawberry": "Strawberry Ice Cream",
    "Waffle Cone": "Waffle Cones",
    "Cake Cone": "Cake Cones",
    "Rainbow Sprinkles": "Rainbow Sprinkles",
    "Hot Fudge": "Hot Fudge Sauce",
    "Caramel": "Caramel Sauce",
    "Whipped Cream": "Whipped Cream",
    "Cherry": "Maraschino Cherries",
}

# Rule 2: how much per (item category, modifier category)?
PORTION_RULES = {
    ("scoops", "flavor"): (Decimal("0.10"), "kg"),
    ("scoops", "cone"): (Decimal("1"), "count"),
    ("scoops", "topping"): (Decimal("0.03"), "kg"),
    ("sundaes", "flavor"): (Decimal("0.15"), "kg"),
    ("sundaes", "topping"): (Decimal("0.03"), "kg"),
}

# Override for modifiers whose unit differs from the category default
MODIFIER_PORTION_OVERRIDES = {
    "Cherry": (Decimal("1"), "count"),
}

# Rule 3: item-level depletions (no modifier, always happen)
ITEM_DEPLETIONS = {
    "Single Scoop": [("Plastic Spoons", Decimal("1"), "count")],
    "Double Scoop": [("Plastic Spoons", Decimal("1"), "count")],
    "Sundae": [
        ("Bowls", Decimal("1"), "count"),
        ("Plastic Spoons", Decimal("1"), "count"),
    ],
}


def seed() -> None:
    with SessionLocal() as session:
        with session.begin():
            session.execute(
                text(
                    "TRUNCATE TABLE bom_lines, mapping_gaps, inventory_transactions, "
                    "catalog_items, catalog_modifiers, inventory_items, "
                    "tenant_configs, tenants, templates, email_outbox "
                    "RESTART IDENTITY CASCADE"
                )
            )

            template = Template(
                slug="icecream-v1",
                version=1,
                body=TemplateConfig().model_dump(),
            )
            session.add(template)
            session.flush()

            tenant = create_tenant(
                name="Dev Shop",
                location="Vancouver",
                shop_type=ShopType.ICE_CREAM,
                session=session,
                template_id=template.id,
            )
            session.flush()

            # Inventory items
            inv_objects = []
            for item in SEED_ITEMS:
                obj = InventoryItem(**item, tenant_id=tenant.id)
                inv_objects.append(obj)
                session.add(obj)
            session.flush()
            inv_by_name = {obj.name: obj for obj in inv_objects}

            # Catalog items
            cat_objects = []
            for item in CATALOG_ITEMS:
                obj = CatalogItem(**item, tenant_id=tenant.id)
                cat_objects.append(obj)
                session.add(obj)
            session.flush()

            # Catalog modifiers
            mod_objects = []
            for mod in CATALOG_MODIFIERS:
                obj = CatalogModifier(**mod, tenant_id=tenant.id)
                mod_objects.append(obj)
                session.add(obj)
            session.flush()

            # Generate BOM lines from rules
            bom_count = 0

            # Modifier-driven depletions (Rule 1 + Rule 2)
            for cat_item in cat_objects:
                for mod in mod_objects:
                    rule_key = (cat_item.category, mod.category)
                    if rule_key not in PORTION_RULES:
                        continue

                    inv_name = MODIFIER_INVENTORY_MAP[mod.name]
                    if mod.name in MODIFIER_PORTION_OVERRIDES:
                        qty, unit = MODIFIER_PORTION_OVERRIDES[mod.name]
                    else:
                        qty, unit = PORTION_RULES[rule_key]

                    session.add(
                        BOMLine(
                            tenant_id=tenant.id,
                            catalog_item_id=cat_item.id,
                            catalog_modifier_id=mod.id,
                            inventory_item_id=inv_by_name[inv_name].id,
                            quantity=qty,
                            unit=unit,
                        )
                    )
                    bom_count += 1

            # Item-level depletions (Rule 3)
            for cat_item in cat_objects:
                if cat_item.name not in ITEM_DEPLETIONS:
                    continue
                for inv_name, qty, unit in ITEM_DEPLETIONS[cat_item.name]:
                    session.add(
                        BOMLine(
                            tenant_id=tenant.id,
                            catalog_item_id=cat_item.id,
                            catalog_modifier_id=None,
                            inventory_item_id=inv_by_name[inv_name].id,
                            quantity=qty,
                            unit=unit,
                        )
                    )
                    bom_count += 1

    log.info(
        "seed_complete",
        tenants=1,
        inventory_items=len(SEED_ITEMS),
        catalog_items=len(CATALOG_ITEMS),
        modifiers=len(CATALOG_MODIFIERS),
        bom_lines=bom_count,
    )


if __name__ == "__main__":
    setup_logging()
    seed()
