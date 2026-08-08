from zoneinfo import ZoneInfo

from sqlalchemy import select

from src.clock import get_now
from src.db.models import Supplier, Tenant
from src.db.session import SessionLocal
from src.logging import get_logger
from src.services.config_services import resolve_config
from src.services.ordering_service import evaluate_promotion

log = get_logger("autonomy_job")


def poll_autonomy() -> None:
    with SessionLocal() as session:
        tenants = session.scalars(select(Tenant)).all()
        utc_time = get_now()
        
        for tenant in tenants:
            suppliers = session.scalars(
                select(Supplier)
                .where(Supplier.tenant_id == tenant.id)
                .where(Supplier.is_active == True)
            ).all()
            
            local_time = utc_time.astimezone(ZoneInfo(tenant.timezone))
            config = resolve_config(str(tenant.id), session)
            
            if local_time.hour < config.schedule.closing_hour:
                continue

            suppliers = [s for s in suppliers if not s.delivery_days]

            for supplier in suppliers:
                evaluate_promotion(session, str(tenant.id), str(supplier.id))
                log.info(
                    "autonomy_evaluated",
                    tenant_id=str(tenant.id),
                    supplier_id=str(supplier.id),
                )