from math import sin, pi
import random
from datetime import datetime, date, timedelta
from uuid import uuid4
from src.events.bus import publish_event
from src.db.models import CatalogItem, CatalogModifier, Tenant
from src.db.session import SessionLocal
from src.schemas.event import EventCategory, SalesEventType
from collections import defaultdict

from src.schemas.sale import SaleEvent, SaleLineItem

DOW_MULTIPLIERS = {0: 0.8, 1: 0.7, 2: 0.9, 3: 0.95, 4: 1.0, 5: 1.2, 6: 1.4}
HOURLY_WEIGHTS = {
    10: 0.03,
    11: 0.06,
    12: 0.10,
    13: 0.12,
    14: 0.15,
    15: 0.15,
    16: 0.13,
    17: 0.10,
    18: 0.08,
    19: 0.05,
    20: 0.03,
}


def generate_daily_volume(gen_date: date) -> int:
    base = 80

    dow = DOW_MULTIPLIERS[gen_date.weekday()]

    temp = 15 + 12 * sin(2 * pi * (gen_date.timetuple().tm_yday - 80) / 365)
    temp += random.uniform(-3, 3)
    temp_factor = 1 + 0.02 * (temp - 20)
    temp_factor = max(0.5, min(temp_factor, 1.8))

    noise = random.uniform(0.85, 1.15)

    return round(base * dow * temp_factor * noise)


def distribute_across_hours(d: date, count: int) -> list[datetime]:
    hours = random.choices(
        population=list(HOURLY_WEIGHTS.keys()),
        weights=list(HOURLY_WEIGHTS.values()),
        k=count,
    )
    timestamps = [
        datetime(d.year, d.month, d.day, h, random.randint(0, 59)) for h in hours
    ]
    return sorted(timestamps)


def build_transaction(
    timestamp: datetime,
    catalog_items: dict,
    modifiers_by_group: dict,
) -> SaleEvent:
    item_type = random.choices(
        population=["Single Scoop", "Double Scoop", "Sundae"],
        weights=[0.60, 0.30, 0.10],
        k=1,
    )[0]

    catalog_item = catalog_items[item_type]
    flavors = modifiers_by_group["flavor"]
    cones = modifiers_by_group.get("cone", [])
    toppings = modifiers_by_group.get("topping", [])

    mods = []
    if item_type == "Double Scoop":
        mods += random.choices(flavors, k=2)
    else:
        mods += random.choices(flavors, k=1)

    if item_type in ("Single Scoop", "Double Scoop") and cones:
        mods += random.choices(cones, k=1)

    if item_type == "Sundae" and toppings:
        mods += random.choices(toppings, k=1)
    elif toppings and random.random() < 0.3:
        mods += random.choices(toppings, k=1)

    return SaleEvent(
        external_transaction_id=str(uuid4()),
        source="synthetic",
        timestamp=timestamp,
        line_items=[
            SaleLineItem(
                item_name=catalog_item.name,
                modifiers=[m.name for m in mods],
                quantity=1,
                unit_price=catalog_item.sale_price,
            )
        ],
        total=catalog_item.sale_price,
        payment_method=random.choices(["card", "cash"], weights=[0.85, 0.15], k=1)[0],
    )


def generate_sales(start_date: date, end_date: date):
    with SessionLocal() as session:
        tenants = session.query(Tenant).all()
        for tenant in tenants:
            transactions = []
            items = (
                session.query(CatalogItem)
                .filter_by(tenant_id=tenant.id, is_active=True)
                .all()
            )
            catalog_items = {item.name: item for item in items}

            mods = session.query(CatalogModifier).filter_by(tenant_id=tenant.id).all()
            modifiers_by_group = defaultdict(list)
            for mod in mods:
                modifiers_by_group[mod.category].append(mod)

            current = start_date
            while current <= end_date:
                count = generate_daily_volume(current)
                for i in distribute_across_hours(current, count):
                    t = build_transaction(
                        i, catalog_items, modifiers_by_group
                    )
                    transactions.append(t)
                current += timedelta(days=1)

            for t in transactions:
                publish_event(
                    EventCategory.SALES,
                    SalesEventType.SALE_COMPLETED.value,
                    "2",
                    t.model_dump(mode="json"),
                    str(tenant.id),
                )
