from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from src.clock import get_now
from src.db.models import Tenant
from src.db.session import SessionLocal
from src.logging import get_logger
from src.services.scheduling_service import calibrate_staffing

log = get_logger("calibrate_schedule_job")


def calibrate_schedule() -> None:
    with SessionLocal() as session:
        tenants = session.scalars(select(Tenant)).all()
        utc_time = get_now()
        for tenant in tenants:
            local_date = utc_time.astimezone(ZoneInfo(tenant.timezone)).date()
            last_monday = local_date - timedelta(days=7)
            calibrate_staffing(session, str(tenant.id), last_monday)
            log.info(
                "calibrate_staffing",
                tenant_id=str(tenant.id),
            )
