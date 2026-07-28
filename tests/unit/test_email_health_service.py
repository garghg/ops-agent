from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from src.adapters.email.dry_run import DryRunSender
from src.db.models.comms import EmailOutbox
from src.db.models.core import Tenant
from src.db.models.health import Heartbeat
from src.schemas.email import EmailStatus
from src.services.email_service import process_outbox
from src.services.health_service import check_heartbeats, record_heartbeat


@pytest.fixture
def tenant(seeded_db):
    return seeded_db.scalar(select(Tenant).limit(1))


class TestProcessOutbox:
    def test_sends_pending_email(self, seeded_db, tenant):
        seeded_db.add(EmailOutbox(
            tenant_id=tenant.id,
            idempotency_key="test-1",
            recipient="test@example.com",
            subject="Test",
            body_html="<p>Hello</p>",
            status=EmailStatus.PENDING.value,
        ))
        seeded_db.flush()

        process_outbox(seeded_db, DryRunSender(), str(tenant.id))

        email = seeded_db.scalar(
            select(EmailOutbox).where(EmailOutbox.idempotency_key == "test-1")
        )
        assert email.status == EmailStatus.SENT.value
        assert email.sent_at is not None

    def test_fails_after_max_attempts(self, seeded_db, tenant):
        class FailSender(DryRunSender):
            def send(self, recipient, subject, body_html):
                return False

        seeded_db.add(EmailOutbox(
            tenant_id=tenant.id,
            idempotency_key="test-fail",
            recipient="test@example.com",
            subject="Test",
            body_html="<p>Hello</p>",
            status=EmailStatus.PENDING.value,
        ))
        seeded_db.flush()

        process_outbox(seeded_db, FailSender(), str(tenant.id))

        email = seeded_db.scalar(
            select(EmailOutbox).where(EmailOutbox.idempotency_key == "test-fail")
        )
        assert email.status == EmailStatus.FAILED.value
        assert email.attempts == 3


class TestRecordHeartbeat:
    def test_upserts_heartbeat(self, seeded_db):
        record_heartbeat(seeded_db, "test_consumer")
        record_heartbeat(seeded_db, "test_consumer")

        beats = seeded_db.scalars(
            select(Heartbeat).where(Heartbeat.consumer_name == "test_consumer")
        ).all()
        assert len(beats) == 1


class TestCheckHeartbeats:
    def test_detects_stale_consumer(self, seeded_db):
        seeded_db.add(Heartbeat(
            consumer_name="dead_consumer",
            last_heartbeat=datetime.now(UTC) - timedelta(minutes=20),
        ))
        seeded_db.flush()

        stale = check_heartbeats(seeded_db, stale_minutes=10)
        assert "dead_consumer" in stale

    def test_healthy_consumer_not_flagged(self, seeded_db):
        record_heartbeat(seeded_db, "alive_consumer")

        stale = check_heartbeats(seeded_db, stale_minutes=10)
        assert "alive_consumer" not in stale