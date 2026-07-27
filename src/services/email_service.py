import time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.adapters import SenderAdapter
from src.db.models.comms import EmailOutbox
from src.schemas.email import EmailStatus


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
        else:
            email.status = EmailStatus.FAILED.value
        
        session.commit()