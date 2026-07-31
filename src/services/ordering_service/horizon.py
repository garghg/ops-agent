from zoneinfo import ZoneInfo

from src.clock import get_now


def protection_horizon(
    delivery_days: list[int],
    lead_time_days: int,
    order_cutoff_hours: int,
    timezone: str,
):

    tz = ZoneInfo(timezone)
    now = get_now()
    local_time = now.astimezone(tz)
    