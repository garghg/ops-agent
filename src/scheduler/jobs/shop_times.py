from zoneinfo import ZoneInfo

from sqlalchemy import select

from scheduler.clock import get_now
from src.db.models import Tenant
from src.db.session import SessionLocal
from src.events.bus import publish_event
from src.logging import get_logger
from src.schemas.event import EventCategory, SystemEventType, WorkforceEventType
from src.services.config_services import resolve_config

log = get_logger("shop_times")


def poll_shop_times() -> None:
    with SessionLocal() as session:
        tenants = session.scalars(select(Tenant)).all()
        log.info("tick_started", tenant_count=len(tenants))
        utc_time = get_now()
        for tenant in tenants:
            local_time = utc_time.astimezone(ZoneInfo(tenant.timezone))
            config = resolve_config(str(tenant.id), session)

            if (
                local_time.hour == config.schedule.opening_hour
                and local_time.minute == config.schedule.opening_min
            ):
                log.info("event_emitted", tenant_id=str(tenant.id), event="DAY_OPENED")
                publish_event(
                    EventCategory.SYSTEM,
                    SystemEventType.DAY_OPENED.value,
                    "4",
                    {},
                    tenant_id=str(tenant.id),
                )

            if (
                local_time.hour == config.schedule.closing_hour
                and local_time.minute == config.schedule.closing_min
            ):
                log.info("event_emitted", tenant_id=str(tenant.id), event="DAY_CLOSED")
                publish_event(
                    EventCategory.SYSTEM,
                    SystemEventType.DAY_CLOSED.value,
                    "4",
                    {"business_date": local_time.date().isoformat()},
                    tenant_id=str(tenant.id),
                )

            if (
                local_time.hour == config.schedule.schedule_gen_hour
                and local_time.minute == config.schedule.schedule_gen_minute
                and local_time.strftime("%a").lower()
                == config.schedule.schedule_gen_day_of_week
            ):
                log.info(
                    "event_emitted",
                    tenant_id=str(tenant.id),
                    event="SCHEDULE_GENERATION_REQUESTED",
                )
                publish_event(
                    EventCategory.WORKFORCE,
                    WorkforceEventType.SCHEDULE_GENERATION_REQUESTED.value,
                    "5",
                    {},
                    tenant_id=str(tenant.id),
                )
