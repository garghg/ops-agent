import random
import uuid
from datetime import time
from decimal import Decimal

from sqlalchemy import text

from simulation.config import PRODUCT_PATH
from simulation.loader import load_products
from src.db.models import (
    AvailabilityRule,
    BOMLine,
    CatalogItem,
    Category,
    Employee,
    InventoryItem,
    Supplier,
    SupplierItem,
    Template,
    Tenant,
)
from src.db.session import SessionLocal
from src.logging import get_logger, setup_logging
from src.schemas.template import TemplateConfig
from src.schemas.tenant import ShopType
from src.schemas.weather import WeatherSource
from src.services.config_services import resolve_config
from src.services.tenant_service import create_tenant
from src.services.weather_service import fetch_actuals, update_db

log = get_logger("seed")


def setup():
    products = load_products(PRODUCT_PATH).values()
    catalog = []
    inventory = []
    suppliers = [
        {
            "name": "Ice Cream Supplier Co.",
            "email": "orders@icecreamsupplier.com",
            "lead_time_days": 2,
            "delivery_days": None,
            "order_cutoff_hours": 15,
            "minimum_order_value": Decimal("50.00"),
        },
        {
            "name": "Soda Supplier Inc.",
            "email": "orders@sodasupplier.com",
            "lead_time_days": 3,
            "delivery_days": None,
            "order_cutoff_hours": 17,
            "minimum_order_value": Decimal("20.00"),
        },
    ]
    employees = [
        {
            "name": "Alice",
            "is_keyholder": True,
            "max_weekly_hours": 40,
            "min_shift_hours": 4,
            "max_shift_hours": 8,
        },
        {
            "name": "Bob",
            "is_keyholder": True,
            "max_weekly_hours": 32,
            "min_shift_hours": 4,
            "max_shift_hours": 8,
        },
        {
            "name": "Charlie",
            "is_keyholder": False,
            "max_weekly_hours": 24,
            "min_shift_hours": 4,
            "max_shift_hours": 6,
        },
        {
            "name": "Diana",
            "is_keyholder": False,
            "max_weekly_hours": 20,
            "min_shift_hours": 4,
            "max_shift_hours": 6,
        },
    ]

    supplier_items = []
    for product in products:
        catalog.append(
            {
                "name": product["name"],
                "sale_price": product["price"],
                "category": product["category"],
            }
        )
        inventory.append(
            {
                "name": product["name"],
                "quantity_on_hand": Decimal(500),
                "reorder_point": Decimal(350),
                "target_quantity": Decimal(750),
                "cost_per_unit": product["price"] * Decimal("0.55"),
                "shelf_life_days": 30 if product["category"] == "ice_cream" else 180,
                "unit": "count",
                "category": product["category"],
            }
        )
        supplier_items.append(
            {
                "item": product["name"],
                "supplier": "Ice Cream Supplier Co."
                if product["category"] == "ice_cream"
                else "Soda Supplier Inc.",
                "pack_size": Decimal("24.00")
                if product["category"] == "ice_cream"
                else Decimal("12.00"),
                "cost_per_unit": product["price"] * Decimal("0.55"),
                "sku": str(uuid.uuid4()),
            }
        )

    with SessionLocal() as session, session.begin():
        session.execute(
            text(
                "TRUNCATE TABLE "
                "anomalies, anomaly_feedback, autonomy_events, "
                "availability_exceptions, availability_rules, "
                "backtest_results, bom_lines, "
                "capability_states, catalog_items, catalog_modifiers, categories, "
                "certifications, correction_factors, count_lines, "
                "daily_actuals, decision_log, "
                "email_outbox, employees, "
                "factor_histories, forecast_metrics, forecasts, "
                "heartbeats, "
                "intraday_profiles, inventory_items, inventory_transactions, "
                "item_demand_forecasts, "
                "mapping_gaps, model_registry, "
                "physical_counts, po_events, po_lines, purchase_orders, "
                "sale_line_items, sale_transactions, "
                "schedule_edits, schedules, share_vectors, shifts, spend_ledger, "
                "supplier_item_cost_history, supplier_items, suppliers, "
                "templates, tenant_configs, tenants, "
                "weather_observations "
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
            name="Ice Cream Shop",
            location="London",
            shop_type=ShopType.ICE_CREAM,
            session=session,
            template_id=str(template.id),
            address="Central Ave., London, UK",
            owner_email="owner@icecreamshop.com",
            timezone="Europe/London"
        )
        session.flush()

        config = resolve_config(str(tenant.id), session)

        cat_by_name = {}
        for name in ["ice_cream", "soft_drink"]:
            obj = Category(tenant_id=tenant.id, name=name)
            session.add(obj)
            cat_by_name[name] = obj
        session.flush()

        inv_objects = []
        for item in inventory:
            item_copy = {**item}
            cat_name = item_copy.pop("category")
            obj = InventoryItem(
                **item_copy, tenant_id=tenant.id, category_id=cat_by_name[cat_name].id
            )
            inv_objects.append(obj)
            session.add(obj)
        session.flush()
        inv_by_name = {obj.name: obj for obj in inv_objects}

        cat_objects = []
        for item in catalog:
            obj = CatalogItem(**item, tenant_id=tenant.id)
            cat_objects.append(obj)
            session.add(obj)
        session.flush()

        sup_objects = []
        for sup in suppliers:
            obj = Supplier(**sup, tenant_id=tenant.id)
            sup_objects.append(obj)
            session.add(obj)
        session.flush()
        sup_by_name = {obj.name: obj for obj in sup_objects}

        sup_item_count = 0
        for item in supplier_items:
            session.add(
                SupplierItem(
                    tenant_id=tenant.id,
                    supplier_id=sup_by_name[item["supplier"]].id,
                    inventory_item_id=inv_by_name[item["item"]].id,
                    pack_size=item["pack_size"],
                    cost_per_unit=item["cost_per_unit"],
                    sku=item.get("sku"),
                )
            )
            sup_item_count += 1

        bom_count = 0
        for inv_item, cat_item in zip(inv_objects, cat_objects):
            session.add(
                BOMLine(
                    tenant_id=tenant.id,
                    catalog_item_id=cat_item.id,
                    catalog_modifier_id=None,
                    inventory_item_id=inv_item.id,
                    quantity=1,
                    unit="count",
                )
            )
            bom_count += 1

        tenant_id = str(tenant.id)

        for emp_data in employees:
            emp = Employee(**emp_data, tenant_id=tenant.id)
            session.add(emp)
            session.flush()

            for dow in range(6):
                session.add(
                    AvailabilityRule(
                        employee_id=emp.id,
                        day_of_week=dow,
                        start_time=time(9, 0),
                        end_time=time(19, 0),
                    )
                )

    log.info(
        "seed_complete",
        tenants=1,
        inventory_items=len(inventory),
        catalog_items=len(catalog),
        bom_lines=bom_count,
        suppliers=len(suppliers),
        supplier_items=sup_item_count,
    )

    return tenant_id, config


def prefetch_weather(tenant_id: str):
    with SessionLocal() as session:
        tenant = session.get(Tenant, tenant_id)
        if not tenant:
            return
        lat = float(tenant.latitude)
        lng = float(tenant.longitude)

    weather = fetch_actuals(lat, lng, "2019-01-01", "2019-12-31")

    with SessionLocal() as session:
        update_db(session, weather, tenant_id, WeatherSource.ACTUAL.value)
        update_db(session, weather, tenant_id, WeatherSource.FORECAST.value)

    log.info("weather_prefetched", days=len(weather), tenant_id=tenant_id)


if __name__ == "__main__":
    setup_logging()
    tenant_id, _ = setup()
    prefetch_weather(tenant_id)
