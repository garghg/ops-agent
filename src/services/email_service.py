import time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.adapters import SenderAdapter
from src.clock import get_now
from src.db.models import EmailOutbox, POEvent, PurchaseOrder
from src.schemas.email import EmailStatus
from src.schemas.orders import OrderBy
from src.schemas.suppliers import POStatus


def process_outbox(session: Session, sender: SenderAdapter, tenant_id: str):
    emails = session.scalars(
        select(EmailOutbox)
        .where(EmailOutbox.tenant_id == tenant_id)
        .where(EmailOutbox.status == EmailStatus.PENDING.value)
    ).all()
    
    if not emails:
        return
    
    for email in emails:        
        is_sent = False
        while not is_sent and email.attempts < 3:
            is_sent = sender.send(email.recipient, email.subject, email.body_html)
            email.attempts += 1
            if not is_sent:
                time.sleep(2)
        
        if is_sent:
            email.status = EmailStatus.SENT.value
            email.sent_at = func.now()
            if email.purchase_order_id:
                po = session.scalar(
                    select(PurchaseOrder)
                    .where(PurchaseOrder.tenant_id == tenant_id)
                    .where(PurchaseOrder.id == email.purchase_order_id)
                )
                old_status = po.status
                po.status = POStatus.SENT.value
                session.add(POEvent(
                    tenant_id=tenant_id,
                    purchase_order_id=email.purchase_order_id,
                    from_status=old_status,
                    to_status=POStatus.SENT.value,
                    changed_by=OrderBy.SYSTEM.value,
                    note="Email sent to supplier",
                    created_at=get_now(),
                ))
        else:
            email.status = EmailStatus.FAILED.value
        
        session.commit()