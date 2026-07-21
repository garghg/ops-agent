from src.clock import get_now
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select

from src.db.models import Tenant
from src.db.session import SessionLocal
from src.events.bus import publish_event
from src.schemas.event import EventCategory, SystemEventType, WorkforceEventType
from src.services.config_services import resolve_config

scheduler = BlockingScheduler()

def poll_shop_times() -> None:
    with SessionLocal() as session:
        tenants = session.scalars(select(Tenant)).all()
        utc_time = get_now()
        for tenant in tenants:
            local_time = utc_time.astimezone(ZoneInfo(tenant.timezone))
            config = resolve_config(str(tenant.id), session)
            
            if (local_time.hour == config.schedule.opening_hour 
                and local_time.minute == config.schedule.opening_min):
                publish_event(
                    EventCategory.SYSTEM,
                    SystemEventType.DAY_OPENED.value,
                    "4",
                    {},
                    tenant_id=str(tenant.id),
                )
            
            if (local_time.hour == config.schedule.closing_hour 
                and local_time.minute == config.schedule.closing_min):
                publish_event(
                    EventCategory.SYSTEM,
                    SystemEventType.DAY_CLOSED.value,
                    "4",
                    {},
                    tenant_id=str(tenant.id),
                )
            
            if (local_time.hour == config.schedule.schedule_gen_hour
                and local_time.minute == config.schedule.schedule_gen_minute
                and local_time.strftime("%a").lower() == config.schedule.schedule_gen_day_of_week):
                publish_event(
                    EventCategory.WORKFORCE,
                    WorkforceEventType.SCHEDULE_GENERATION_REQUESTED.value,
                    "5",
                    {},
                    tenant_id=str(tenant.id),
                )

scheduler.add_job(poll_shop_times, CronTrigger(minute="*"))

if __name__ == "__main__": 
    scheduler.start()