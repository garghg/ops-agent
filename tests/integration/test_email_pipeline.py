import json

from sqlalchemy import select

from src.adapters.email.dry_run import DryRunSender
from src.consumers.email_consumer import process_events
from src.db.models import EmailOutbox, InventoryItem, Tenant
from src.schemas.email import EmailStatus
from src.schemas.suppliers import POStatus
from src.services.email_service import process_outbox
from src.services.ordering_service import generate_proposals


def test_approval_to_email(seeded_db):
    tenant = seeded_db.scalar(select(Tenant).limit(1))

    item = seeded_db.scalar(
        select(InventoryItem)
        .where(InventoryItem.tenant_id == tenant.id)
        .where(InventoryItem.name == "Chocolate Ice Cream")
    )

    pos = generate_proposals(seeded_db, tenant.id, [str(item.id)])
    assert len(pos) == 1
    po = pos[0]

    po.status = POStatus.APPROVED.value
    seeded_db.flush()

    fake_event = {
        "id": "test-event-1",
        "payload": {"purchase_order_id": str(po.id), "changed_by": "owner"},
        "tenant_id": str(tenant.id),
        "event_type": "po_approved",
    }

    process_events([fake_event])

    email = seeded_db.scalar(
        select(EmailOutbox)
        .where(EmailOutbox.tenant_id == tenant.id)
        .where(EmailOutbox.idempotency_key == f"po-order-{po.id}")
    )
    assert email is not None
    assert email.status == EmailStatus.PENDING.value
    assert "Chocolate" in email.body_html or "Dairy" in email.subject

    process_outbox(seeded_db, DryRunSender(), str(tenant.id))

    seeded_db.refresh(email)
    assert email.status == EmailStatus.SENT.value
