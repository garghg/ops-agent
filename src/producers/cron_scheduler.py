from sqlalchemy import select
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from src.config import (
    OPENING_HOUR,
    OPENING_MINUTE,
    CLOSING_HOUR,
    CLOSING_MINUTE,
    SCHEDULE_GEN_DAY_OF_WEEK,
    SCHEDULE_GEN_HOUR,
    SCHEDULE_GEN_MINUTE,
)
from src.events.bus import publish_event
from src.db.models import Tenant
from src.db.session import SessionLocal
from src.schemas.event import EventCategory, SystemEventType, WorkforceEventType

scheduler = BlockingScheduler()


def get_all_tenant_ids() -> list[str]:
    with SessionLocal() as session:
        tenants = session.scalars(select(Tenant)).all()
        return [str(t.id) for t in tenants]


def day_open() -> None:
    for tenant_id in get_all_tenant_ids():
        publish_event(
            EventCategory.SYSTEM,
            SystemEventType.DAY_OPENED.value,
            "4",
            {},
            tenant_id=tenant_id,
        )


def day_close() -> None:
    for tenant_id in get_all_tenant_ids():
        publish_event(
            EventCategory.SYSTEM,
            SystemEventType.DAY_CLOSED.value,
            "4",
            {},
            tenant_id=tenant_id,
        )


def request_schedule_generation() -> None:
    for tenant_id in get_all_tenant_ids():
        publish_event(
            EventCategory.WORKFORCE,
            WorkforceEventType.SCHEDULE_GENERATION_REQUESTED.value,
            "5",
            {},
            tenant_id=tenant_id,
        )


scheduler.add_job(day_open, CronTrigger(hour=OPENING_HOUR, minute=OPENING_MINUTE))
scheduler.add_job(day_close, CronTrigger(hour=CLOSING_HOUR, minute=CLOSING_MINUTE))
scheduler.add_job(
    request_schedule_generation,
    CronTrigger(
        day_of_week=SCHEDULE_GEN_DAY_OF_WEEK,
        hour=SCHEDULE_GEN_HOUR,
        minute=SCHEDULE_GEN_MINUTE,
    ),
)

if __name__ == "__main__":
    scheduler.start()
