from datetime import timedelta
from zoneinfo import ZoneInfo

from src.clock import get_now


def protection_horizon(
    delivery_days: list[int] | None,
    lead_time_days: int | None,
    order_cutoff_hours: int,
    timezone: str,
):

    tz = ZoneInfo(timezone)
    now = get_now()
    local_time = now.astimezone(tz)
    local_hour = local_time.hour
    today = local_time.date()
    
    if delivery_days:
        next_delivery = today + timedelta(days=1)
        while next_delivery.weekday() not in delivery_days:
            next_delivery += timedelta(days=1)
        return (today, next_delivery - timedelta(days=1))
    elif lead_time_days:
        order_date = today
        if local_hour >= order_cutoff_hours:
            order_date += timedelta(days=1)
        second_arrival = order_date + timedelta(days=1) + timedelta(days=lead_time_days)
        return (today, second_arrival - timedelta(days=1))
    else:
        return None