import os
import threading
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from simulation.config import PRODUCT_PATH, TRANSACTION_PATH
from simulation.loader import load_products, load_transactions
from simulation.operator import (
    handle_anomalies,
    handle_autonomy,
    handle_cycle_counts,
    handle_deliveries,
    handle_proposals,
    handle_sent_orders,
)
from simulation.setup import prefetch_weather, setup
from src.clock import set_virtual_time
from src.consumers.anomaly_consumer import anomaly_consumer
from src.consumers.bom_consumer import bom_consumer
from src.consumers.email_consumer import email_consumer
from src.consumers.forecast_consumer import forecast_consumer
from src.consumers.sales_consumer import sales_consumer
from src.consumers.stock_updater import stock_updater
from src.consumers.summary_consumer import summary_consumer
from src.db.models import InventoryItem
from src.db.session import SessionLocal
from src.events.bus import publish_event, r
from src.logging import get_logger, setup_logging
from src.scheduler.jobs import (
    calibrate_schedule,
    poll_autonomy,
    poll_models,
    poll_proposals,
    poll_shop_times,
    sweep_outbox,
)
from src.schemas.event import EventCategory

log = get_logger("simulator")

CONSUMERS = [
    ("stock_updater", stock_updater),
    ("sales_consumer", sales_consumer),
    ("bom_consumer", bom_consumer),
    ("email_consumer", email_consumer),
    ("forecast_consumer", forecast_consumer),
    ("anomaly_consumer", anomaly_consumer),
    ("summary_consumer", summary_consumer),
]


def _set_time(day_date: date, hour: int, minute: int, tz: str = "Europe/London"):
    day_dt = datetime(
        day_date.year, day_date.month, day_date.day, hour, minute, tzinfo=ZoneInfo(tz)
    )
    set_virtual_time(day_dt)


def _wait_for_consumers(max_wait: int = 30):
    streams = [f"{cat.value}_events" for cat in EventCategory]

    for _ in range(max_wait):
        total = 0
        for stream in streams:
            try:
                groups = r.xinfo_groups(stream)
                for g in groups:
                    total += g["pending"]
                    total += g.get("lag", 0) or 0
            except Exception:  # noqa: BLE001, S112
                continue
        if total == 0:
            return
        time.sleep(1)

    log.warning("consumers_still_behind", remaining=total, max_wait=max_wait) # type: ignore

def run_consumers():
    setup_logging()
    log.info("starting_system", consumers=len(CONSUMERS))

    for name, fn in CONSUMERS:
        t = threading.Thread(target=fn, name=name, daemon=True)
        t.start()
        log.info("consumer_started", consumer=name)


if __name__ == "__main__":
    setup_logging()

    os.system("powershell.exe '[console]::beep(1000,500)'")
    log.info("setting up database")
    tenant_id, config = setup()
    prefetch_weather(tenant_id)

    r.flushdb()
    log.info("redis_flushed")

    run_consumers()
    time.sleep(2)

    transactions = load_transactions(TRANSACTION_PATH)

    LIMIT_DAYS = 30
    transactions = dict(list(transactions.items())[:LIMIT_DAYS])

    products = load_products(PRODUCT_PATH)

    for day_date, day_df in transactions.items():
        _set_time(day_date, config.schedule.opening_hour, config.schedule.opening_min)
        poll_shop_times()

        with SessionLocal() as session:
            stock = {
                row.name: float(row.quantity_on_hand)
                for row in session.scalars(
                    select(InventoryItem).where(InventoryItem.tenant_id == tenant_id)
                ).all()
            }
            log.info("stock_taken")

        for i, row in day_df.iterrows():
            gtin = str(row["gtin"])
            name = products[gtin]["name"]
            if stock.get(name, 0) <= 0:
                continue
            stock[name] -= 1

            sale_payload = {
                "external_transaction_id": f"{gtin}_{row['sales_date_time']}_{i}",
                "source": "simulation",
                "timestamp": row["sales_date_time"].isoformat(),
                "total": str(products[gtin]["price"]),
                "payment_method": "card",
                "line_items": [
                    {
                        "item_name": products[gtin]["name"],
                        "unit_price": str(products[gtin]["price"]),
                        "quantity": 1,
                    }
                ],
            }

            publish_event(
                EventCategory.SALES,
                "sale_completed",
                "3",
                sale_payload,
                tenant_id,
            )

        _wait_for_consumers()
            
        _set_time(day_date, 14, 0)
        poll_shop_times()
        poll_proposals()
        _set_time(day_date, config.schedule.closing_hour, config.schedule.closing_min)
        poll_shop_times()
        _wait_for_consumers()
        sweep_outbox()
        poll_autonomy()
        if day_date.weekday() == 0:
            poll_models()
            calibrate_schedule()
        handle_proposals(tenant_id, day_date)
        handle_sent_orders(tenant_id)
        handle_deliveries(tenant_id, day_date)
        handle_anomalies(tenant_id, day_date)
        handle_autonomy(tenant_id, day_date)
        handle_cycle_counts(tenant_id, day_date)
        log.info(f"{day_date} - day complete")


    log.info("simulation_complete")
    os.system("powershell.exe '[console]::beep(1000,500)'")