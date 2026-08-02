from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.clock import get_now
from src.db.models import ItemDemandForecast
from src.services.forecast_service.config import QUANTILE_LABELS


def protection_horizon(
    lead_time_days: int,
    order_cutoff_hours: int,
    timezone: str,
):

    tz = ZoneInfo(timezone)
    now = get_now()
    local_time = now.astimezone(tz)
    local_hour = local_time.hour
    today = local_time.date()

    order_date = today
    if local_hour >= order_cutoff_hours:
        order_date += timedelta(days=1)
    next_possible_arrival = (
        order_date + timedelta(days=1) + timedelta(days=lead_time_days)
    )
    return (today, next_possible_arrival - timedelta(days=1))


def horizon_aggregate(
    session: Session, item_id: str, tenant_id: str, start_date: date, end_date: date
):
    total_days = (end_date - start_date).days + 1
    present = 0
    sum_pe = Decimal(0)
    sum_qg = {key: Decimal(0) for key in QUANTILE_LABELS}

    cur_date = start_date
    while cur_date <= end_date:
        row = session.execute(
            select(ItemDemandForecast.point_estimate, ItemDemandForecast.quantile_grid)
            .where(ItemDemandForecast.inventory_item_id == item_id)
            .where(ItemDemandForecast.tenant_id == tenant_id)
            .where(ItemDemandForecast.target_date == cur_date)
        ).first()

        if row:
            sum_pe += row.point_estimate
            for key in QUANTILE_LABELS:
                sum_qg[key] += Decimal(str(row.quantile_grid[key]))
            present += 1

        cur_date += timedelta(days=1)

    if present == 0:
        return

    scale = Decimal(total_days) / Decimal(present)
    aggregate_pe = sum_pe * scale
    aggregate_qg = {key: sum_qg[key] * scale for key in QUANTILE_LABELS}

    return (aggregate_pe, aggregate_qg)
