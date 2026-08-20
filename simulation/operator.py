from datetime import date
import random
import time

from sqlalchemy import select

from schemas.suppliers import POStatus
from src.db.session import SessionLocal
from src.db.models import PurchaseOrder, POEvent

def handle_proposals(tenant_id: str, business_date: date):

    with SessionLocal() as session, session.begin():
        proposed = session.scalars(
            select(PurchaseOrder)
            .where(PurchaseOrder.tenant_id == tenant_id)
            .where(PurchaseOrder.status == POStatus.PROPOSED.value)
        ).all()

        if not proposed:
            return

        for proposal in proposed:
            chance = random.uniform(0, 1)

            if chance <= 0.75:
                proposal.status = POStatus.APPROVED
                session.add(POEvent(
                    tenant_id=tenant_id,
                    purchase_order_id=proposal.id,
                    from_status=POStatus.PROPOSED.value,
                    to_status=POStatus.APPROVED.value,
                    changed_by="system",
                ))
                proposal.status = POStatus.SENT
                session.add(POEvent(
                    tenant_id=tenant_id,
                    purchase_order_id=proposal.id,
                    from_status=POStatus.APPROVED.value,
                    to_status=POStatus.SENT.value,
                    changed_by="system",
                ))
                proposal.status = POStatus.CONFIRMED
                session.add(POEvent(
                    tenant_id=tenant_id,
                    purchase_order_id=proposal.id,
                    from_status=POStatus.SENT.value,
                    to_status=POStatus.CONFIRMED.value,
                    changed_by="system",
                ))