from sqlalchemy import select

from src.adapters.email import get_sender
from src.db.models import Tenant
from src.db.session import SessionLocal
from src.services.email_service import process_outbox


def sweep_outbox() -> None:
    sender = get_sender()
    with SessionLocal() as session:
        tenants = session.scalars(select(Tenant)).all()
    for tenant in tenants:
        with SessionLocal() as session:
            process_outbox(session, sender, str(tenant.id))
