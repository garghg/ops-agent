from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from src.consumers.summary_consumer import process_events
from src.db.models import EmailOutbox, SaleTransaction, Tenant


@pytest.fixture
def tenant(seeded_db):
    return seeded_db.scalar(select(Tenant).limit(1))


def _make_day_closed_event(tenant_id, business_date="2026-07-28"):
    return {
        "id": "fake-stream-id-summary",
        "event_type": "day_closed",
        "tenant_id": str(tenant_id),
        "priority": "4",
        "payload": {"business_date": business_date},
    }


def _make_day_opened_event(tenant_id):
    return {
        "id": "fake-stream-id-opened",
        "event_type": "day_opened",
        "tenant_id": str(tenant_id),
        "priority": "4",
        "payload": {},
    }


def _seed_sales(session, tenant, business_date="2026-07-28"):
    session.add(SaleTransaction(
        external_transaction_id="summary-test-001",
        source="synthetic",
        timestamp=datetime.fromisoformat(f"{business_date}T14:30:00+00:00"),
        total=Decimal("25.00"),
        payment_method="card",
        transaction_type="sale",
        discount_amount=Decimal("2.00"),
        tenant_id=tenant.id,
    ))
    session.add(SaleTransaction(
        external_transaction_id="summary-test-002",
        source="synthetic",
        timestamp=datetime.fromisoformat(f"{business_date}T15:00:00+00:00"),
        total=Decimal("10.00"),
        payment_method="card",
        transaction_type="void",
        discount_amount=Decimal(0),
        tenant_id=tenant.id,
    ))
    session.commit()


class TestSummaryHappyPath:
    def test_creates_outbox_row_with_summary(self, seeded_db, tenant):
        _seed_sales(seeded_db, tenant)
        event = _make_day_closed_event(tenant.id)

        process_events([event])

        outbox = seeded_db.scalar(
            select(EmailOutbox).where(
                EmailOutbox.tenant_id == tenant.id,
                EmailOutbox.idempotency_key == f"summary-{tenant.id}-2026-07-28",
            )
        )
        assert outbox is not None
        assert outbox.recipient == tenant.owner_email
        assert "Daily Summary" in outbox.subject


class TestNoOwnerEmail:
    def test_skips_when_no_owner_email(self, seeded_db, tenant):
        original_email = tenant.owner_email
        tenant.owner_email = None
        seeded_db.commit()

        event = _make_day_closed_event(tenant.id)
        process_events([event])

        outbox = seeded_db.scalar(
            select(EmailOutbox).where(
                EmailOutbox.tenant_id == tenant.id,
                EmailOutbox.idempotency_key == f"summary-{tenant.id}-2026-07-28",
            )
        )
        assert outbox is None

        tenant.owner_email = original_email
        seeded_db.commit()


class TestNonDayClosedSkipped:
    def test_day_opened_event_ignored(self, seeded_db, tenant):
        event = _make_day_opened_event(tenant.id)

        process_events([event])

        count = len(
            seeded_db.scalars(
                select(EmailOutbox).where(
                    EmailOutbox.tenant_id == tenant.id,
                )
            ).all()
        )
        assert count == 0
        
class TestSummaryIdempotency:
    def test_duplicate_day_closed_produces_single_outbox(self, seeded_db, tenant):
        _seed_sales(seeded_db, tenant)
        event = _make_day_closed_event(tenant.id)

        process_events([event])
        process_events([event])

        count = len(
            seeded_db.scalars(
                select(EmailOutbox).where(
                    EmailOutbox.tenant_id == tenant.id,
                    EmailOutbox.idempotency_key == f"summary-{tenant.id}-2026-07-28",
                )
            ).all()
        )
        assert count == 1


class TestZeroSalesDay:
    def test_summary_created_with_no_sales(self, seeded_db, tenant):
        event = _make_day_closed_event(tenant.id, business_date="2026-01-01")

        process_events([event])

        outbox = seeded_db.scalar(
            select(EmailOutbox).where(
                EmailOutbox.tenant_id == tenant.id,
                EmailOutbox.idempotency_key == f"summary-{tenant.id}-2026-01-01",
            )
        )
        assert outbox is not None