from datetime import date, timedelta

import pytest
from sqlalchemy import select

from src.consumers.email_consumer import process_events as email_process_events
from src.db.models import (
    EmailOutbox,
    InventoryItem,
    POEvent,
    POLine,
    PurchaseOrder,
    Supplier,
    Tenant,
)
from src.events.bus import create_group, publish_event, read_event
from src.schemas.email import EmailStatus
from src.schemas.event import ConsumerGroup, EventCategory, ProcurementEventType
from src.schemas.orders import OrderBy
from src.schemas.suppliers import POStatus
from src.services.email_service import process_outbox
from src.services.ordering_service import generate_proposals, rollup


class FakeSender:
    def __init__(self):
        self.sent = []

    def send(self, recipient, subject, body_html):
        self.sent.append({"recipient": recipient, "subject": subject})
        return True


@pytest.fixture
def tenant(seeded_db):
    return seeded_db.scalar(select(Tenant).limit(1))


@pytest.fixture
def dairy_supplier(seeded_db, tenant):
    return seeded_db.scalar(
        select(Supplier)
        .where(Supplier.tenant_id == tenant.id)
        .where(Supplier.name == "Dairy Farms Co-op")
    )


@pytest.fixture
def chocolate(seeded_db, tenant):
    return seeded_db.scalar(
        select(InventoryItem)
        .where(InventoryItem.tenant_id == tenant.id)
        .where(InventoryItem.name == "Chocolate Ice Cream")
    )


class TestE2EOrderFlow:
    def test_full_order_lifecycle(
        self, seeded_db, tenant, dairy_supplier, chocolate, flush_redis
    ):
        create_group(EventCategory.PROCUREMENT, ConsumerGroup.EMAIL_CONSUMER.value)

        # 1. Generate proposal
        proposals = generate_proposals(seeded_db, tenant.id, [str(chocolate.id)])
        assert len(proposals) == 1
        po = proposals[0]
        assert po.status == POStatus.PROPOSED.value

        # 2. Owner approves
        po.status = POStatus.APPROVED.value
        seeded_db.add(
            POEvent(
                tenant_id=tenant.id,
                purchase_order_id=po.id,
                from_status=POStatus.PROPOSED.value,
                to_status=POStatus.APPROVED.value,
                changed_by=OrderBy.OWNER.value,
                note="Approved via test",
            )
        )
        seeded_db.commit()

        publish_event(
            EventCategory.PROCUREMENT,
            ProcurementEventType.PO_APPROVED.value,
            "2",
            {"purchase_order_id": str(po.id), "changed_by": OrderBy.OWNER.value},
            str(tenant.id),
        )

        # 3. Email consumer creates outbox row
        events = read_event(
            EventCategory.PROCUREMENT,
            ConsumerGroup.EMAIL_CONSUMER.value,
            "test",
            count=10,
            block_ms=500,
        )
        email_process_events(events)

        seeded_db.expire_all()
        email = seeded_db.scalar(
            select(EmailOutbox)
            .where(EmailOutbox.purchase_order_id == po.id)
            .where(EmailOutbox.tenant_id == tenant.id)
        )
        assert email is not None
        assert email.status == EmailStatus.PENDING.value

        # 4. Outbox sends email → PO transitions to SENT
        sender = FakeSender()
        process_outbox(seeded_db, sender, str(tenant.id))
        assert len(sender.sent) == 1
        assert sender.sent[0]["recipient"] == dairy_supplier.email

        seeded_db.expire_all()
        po = seeded_db.scalar(
            select(PurchaseOrder).where(PurchaseOrder.id == po.id)
        )
        assert po.status == POStatus.SENT.value

        # 5. Owner confirms with no edits
        po.status = POStatus.CONFIRMED.value
        po.expected_delivery = date.today() + timedelta(days=2)  # noqa: DTZ011
        seeded_db.add(
            POEvent(
                tenant_id=tenant.id,
                purchase_order_id=po.id,
                from_status=POStatus.SENT.value,
                to_status=POStatus.CONFIRMED.value,
                changed_by=OrderBy.OWNER.value,
                note="Confirmed via test",
            )
        )
        seeded_db.commit()

        # 6. Owner receives
        po_lines = seeded_db.scalars(
            select(POLine)
            .where(POLine.purchase_order_id == po.id)
            .where(POLine.tenant_id == tenant.id)
        ).all()

        for line in po_lines:
            line.quantity_received = line.quantity_ordered

        po.status = POStatus.RECEIVED.value
        po.actual_delivery = date.today()  # noqa: DTZ011
        seeded_db.add(
            POEvent(
                tenant_id=tenant.id,
                purchase_order_id=po.id,
                from_status=POStatus.CONFIRMED.value,
                to_status=POStatus.RECEIVED.value,
                changed_by=OrderBy.OWNER.value,
                note="Received via test",
            )
        )
        seeded_db.commit()

        # 7. Verify full event chain
        seeded_db.expire_all()
        all_events = seeded_db.scalars(
            select(POEvent)
            .where(POEvent.purchase_order_id == po.id)
            .where(POEvent.tenant_id == tenant.id)
            .order_by(POEvent.created_at)
        ).all()

        statuses = [(e.from_status, e.to_status) for e in all_events]
        assert (None, POStatus.PROPOSED.value) in statuses
        assert (POStatus.PROPOSED.value, POStatus.APPROVED.value) in statuses
        assert (POStatus.APPROVED.value, POStatus.SENT.value) in statuses
        assert (POStatus.SENT.value, POStatus.CONFIRMED.value) in statuses
        assert (POStatus.CONFIRMED.value, POStatus.RECEIVED.value) in statuses

        # 8. Verify rollup sees the approval
        stats = rollup(seeded_db, str(tenant.id), str(dairy_supplier.id))
        assert stats is not None
        assert stats["proposal_count"] >= 1
        assert stats["consecutive_rejects"] == 0

        # 9. Final PO state
        final_po = seeded_db.scalar(
            select(PurchaseOrder).where(PurchaseOrder.id == po.id)
        )
        assert final_po.status == POStatus.RECEIVED.value
        assert final_po.actual_delivery is not None