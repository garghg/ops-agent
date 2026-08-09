from zoneinfo import ZoneInfo

from sqlalchemy import select

from src.clock import get_now
from src.db.models import Tenant
from src.db.session import SessionLocal
from src.logging import get_logger
from src.schemas.models import ModelVersion
from src.services.learning_service import champion_eval

log = get_logger("update_models_job")


def poll_models() -> None:
    with SessionLocal() as session:
        tenants = session.scalars(select(Tenant)).all()
        utc_time = get_now()
        models = [
            model.value
            for model in ModelVersion
            if model.value
            not in [
                ModelVersion.TRAILING_7D_MEAN.value,
                ModelVersion.SEASONAL_NAIVE.value,
            ]
        ]
        for tenant in tenants:
            local_date = utc_time.astimezone(ZoneInfo(tenant.timezone)).date()
            for model in models:
                champion_eval(session, str(tenant.id), model, local_date)
                log.info(
                    "champion_evaluated",
                    tenant_id=str(tenant.id),
                    model=model,
                )
