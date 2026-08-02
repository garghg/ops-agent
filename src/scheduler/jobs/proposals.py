from zoneinfo import ZoneInfo

from sqlalchemy import select

from src.clock import get_now
from src.db.models import InventoryItem, Tenant
from src.db.session import SessionLocal
from src.logging import get_logger
from src.services.config_services import resolve_config
from src.services.ordering_service import generate_proposals

log = get_logger("proposals_job")


def poll_proposals() -> None:
    with SessionLocal() as session:
        tenants = session.scalars(select(Tenant)).all()
        utc_time = get_now()

        for tenant in tenants:
            local_time = utc_time.astimezone(ZoneInfo(tenant.timezone))
            config = resolve_config(str(tenant.id), session)

            if not (config.schedule.opening_hour <= local_time.hour < config.schedule.closing_hour):
                continue

            item_ids = [
                str(item_id)
                for item_id in session.scalars(
                    select(InventoryItem.id).where(InventoryItem.tenant_id == tenant.id)
                ).all()
            ]

            if not item_ids:
                continue

            proposals = generate_proposals(session, tenant.id, item_ids)
            log.info(
                "proposals_generated",
                tenant_id=str(tenant.id),
                count=len(proposals),
            )