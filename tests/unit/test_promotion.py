from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from src.db.models import (
    AutonomyEvent,
    CapabilityState,
    POEvent,
    PurchaseOrder,
    Supplier,
    Tenant,
)
from src.schemas.autonomy import AutonomyEventType, AutonomyState
from src.schemas.orders import OrderBy
from src.schemas.suppliers import POStatus
from src.services.ordering_service import (
    evaluate_demotion,
    evaluate_promotion,
    rollup,
)

UTC = datetime.now().astimezone().tzinfo

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

def _make_po(
    session,
    tenant_id,
    supplier_id,
    status=POStatus.PROPOSED.value,
    total_value=Decimal("100.00"),
):
    po = PurchaseOrder(
        tenant_id=tenant_id,
        supplier_id=supplier_id,
        status=status,
        total_value=total_value,
        created_by=OrderBy.SYSTEM.value,
    )
    session.add(po)
    session.flush()
    return po


def _make_event(
    session,
    tenant_id,
    po,
    from_status,
    to_status,
    changed_by,
    created_at=None,
    edits=None,
):
    evt = POEvent(
        tenant_id=tenant_id,
        purchase_order_id=po.id,
        from_status=from_status,
        to_status=to_status,
        changed_by=changed_by,
        note="test event",
        edits=edits,
    )
    session.add(evt)
    session.flush()
    if created_at:
        session.execute(
            POEvent.__table__.update()
            .where(POEvent.id == evt.id)
            .values(created_at=created_at)
        )
        session.flush()
    return evt


class TestRollupCounts:
    def test_rollup_returns_correct_counts(self, seeded_db, tenant, dairy_supplier):
        now = datetime.now(tz=UTC)

        for i in range(5):
            po = _make_po(seeded_db, tenant.id, dairy_supplier.id)
            _make_event(
                seeded_db,
                tenant.id,
                po,
                None,
                POStatus.PROPOSED.value,
                OrderBy.SYSTEM.value,
                created_at=now - timedelta(days=25 - i),
            )

        approved_pos = []
        for i in range(4):
            po = _make_po(seeded_db, tenant.id, dairy_supplier.id)
            _make_event(
                seeded_db,
                tenant.id,
                po,
                POStatus.PROPOSED.value,
                POStatus.APPROVED.value,
                OrderBy.OWNER.value,
                created_at=now - timedelta(days=20 - i),
            )
            approved_pos.append(po)

        rejected_po = _make_po(seeded_db, tenant.id, dairy_supplier.id)
        _make_event(
            seeded_db,
            tenant.id,
            rejected_po,
            POStatus.PROPOSED.value,
            POStatus.CANCELLED.value,
            OrderBy.OWNER.value,
            created_at=now - timedelta(days=1),
        )

        seeded_db.commit()

        stats = rollup(seeded_db, str(tenant.id), str(dairy_supplier.id))
        assert stats is not None
        assert stats["proposal_count"] == 5
        assert stats["approval_rate"] == 4 / 5
        assert stats["consecutive_rejects"] == 1
        assert stats["critical_failures"] == 0


class TestRollupEditMagnitude:
    def test_rollup_computes_edit_median_and_max(
        self, seeded_db, tenant, dairy_supplier
    ):
        po1 = _make_po(seeded_db, tenant.id, dairy_supplier.id)
        _make_event(
            seeded_db,
            tenant.id,
            po1,
            POStatus.PROPOSED.value,
            POStatus.PROPOSED.value,
            OrderBy.OWNER.value,
            edits=[{"item": "Milk", "from": 10.0, "to": 11.0}],
        )

        po2 = _make_po(seeded_db, tenant.id, dairy_supplier.id)
        _make_event(
            seeded_db,
            tenant.id,
            po2,
            POStatus.PROPOSED.value,
            POStatus.PROPOSED.value,
            OrderBy.OWNER.value,
            edits=[{"item": "Cream", "from": 20.0, "to": 24.0}],
        )

        po3 = _make_po(seeded_db, tenant.id, dairy_supplier.id)
        _make_event(
            seeded_db,
            tenant.id,
            po3,
            POStatus.PROPOSED.value,
            POStatus.PROPOSED.value,
            OrderBy.OWNER.value,
            edits=[{"item": "Butter", "from": 10.0, "to": 15.0}],
        )

        seeded_db.commit()

        stats = rollup(seeded_db, str(tenant.id), str(dairy_supplier.id))
        # magnitudes: 0.10, 0.20, 0.50 → median = 0.20, max = 0.50
        assert stats["edit_median"] == pytest.approx(0.20)
        assert stats["max_edit"] == pytest.approx(0.50)


class TestRollupIgnoresConfirmEdits:
    def test_confirm_edits_excluded_from_magnitude(
        self, seeded_db, tenant, dairy_supplier
    ):
        po = _make_po(
            seeded_db, tenant.id, dairy_supplier.id, status=POStatus.CONFIRMED.value
        )
        _make_event(
            seeded_db,
            tenant.id,
            po,
            POStatus.SENT.value,
            POStatus.CONFIRMED.value,
            OrderBy.OWNER.value,
            edits=[{"item": "Milk", "from": 10.0, "to": 50.0}],
        )
        seeded_db.commit()

        stats = rollup(seeded_db, str(tenant.id), str(dairy_supplier.id))
        if stats:
            assert stats["edit_median"] is None
            assert stats["max_edit"] is None


class TestPromotionProposed:
    def test_promotion_proposed_when_gates_pass(
        self, seeded_db, tenant, dairy_supplier
    ):
        now = datetime.now(tz=UTC)

        for i in range(14):
            po = _make_po(seeded_db, tenant.id, dairy_supplier.id)
            _make_event(
                seeded_db,
                tenant.id,
                po,
                None,
                POStatus.PROPOSED.value,
                OrderBy.SYSTEM.value,
                created_at=now - timedelta(days=28 - i * 2),
            )
            _make_event(
                seeded_db,
                tenant.id,
                po,
                POStatus.PROPOSED.value,
                POStatus.APPROVED.value,
                OrderBy.OWNER.value,
                created_at=now - timedelta(days=28 - i, hours=-1),
            )

        seeded_db.commit()

        evaluate_promotion(seeded_db, str(tenant.id), str(dairy_supplier.id))

        event = seeded_db.scalar(
            select(AutonomyEvent)
            .where(AutonomyEvent.tenant_id == tenant.id)
            .where(AutonomyEvent.supplier_id == dairy_supplier.id)
            .where(
                AutonomyEvent.event_type == AutonomyEventType.PROMOTION_PROPOSED.value
            )
        )
        assert event is not None
        assert "proposal_count" in event.reason
        assert event.from_state == AutonomyState.PROPOSE_ONLY.value
        assert event.to_state == AutonomyState.PROPOSE_ONLY.value


class TestPromotionSkipped:
    def test_promotion_not_proposed_when_insufficient_proposals(
        self, seeded_db, tenant, dairy_supplier
    ):
        now = datetime.now(tz=UTC)

        for i in range(3):
            po = _make_po(seeded_db, tenant.id, dairy_supplier.id)
            _make_event(
                seeded_db,
                tenant.id,
                po,
                None,
                POStatus.PROPOSED.value,
                OrderBy.SYSTEM.value,
                created_at=now - timedelta(days=10 - i),
            )
            _make_event(
                seeded_db,
                tenant.id,
                po,
                POStatus.PROPOSED.value,
                POStatus.APPROVED.value,
                OrderBy.OWNER.value,
                created_at=now - timedelta(days=10 - i, hours=-1),
            )

        seeded_db.commit()

        evaluate_promotion(seeded_db, str(tenant.id), str(dairy_supplier.id))

        event = seeded_db.scalar(
            select(AutonomyEvent)
            .where(AutonomyEvent.tenant_id == tenant.id)
            .where(AutonomyEvent.supplier_id == dairy_supplier.id)
            .where(
                AutonomyEvent.event_type == AutonomyEventType.PROMOTION_PROPOSED.value
            )
        )
        assert event is None


class TestPromotionDedup:
    def test_only_one_promotion_per_day(self, seeded_db, tenant, dairy_supplier):
        now = datetime.now(tz=UTC)

        for i in range(14):
            po = _make_po(seeded_db, tenant.id, dairy_supplier.id)
            _make_event(
                seeded_db,
                tenant.id,
                po,
                None,
                POStatus.PROPOSED.value,
                OrderBy.SYSTEM.value,
                created_at=now - timedelta(days=28 - i * 2),
            )
            _make_event(
                seeded_db,
                tenant.id,
                po,
                POStatus.PROPOSED.value,
                POStatus.APPROVED.value,
                OrderBy.OWNER.value,
                created_at=now - timedelta(days=28 - i, hours=-1),
            )

        seeded_db.commit()

        evaluate_promotion(seeded_db, str(tenant.id), str(dairy_supplier.id))
        evaluate_promotion(seeded_db, str(tenant.id), str(dairy_supplier.id))

        events = seeded_db.scalars(
            select(AutonomyEvent)
            .where(AutonomyEvent.tenant_id == tenant.id)
            .where(AutonomyEvent.supplier_id == dairy_supplier.id)
            .where(
                AutonomyEvent.event_type == AutonomyEventType.PROMOTION_PROPOSED.value
            )
        ).all()
        assert len(events) == 1


class TestDemotionOnRejectStreak:
    def test_demotes_after_three_consecutive_rejections(
        self, seeded_db, tenant, dairy_supplier
    ):
        seeded_db.add(
            CapabilityState(
                tenant_id=tenant.id,
                supplier_id=dairy_supplier.id,
                state=AutonomyState.AUTO_WITHIN_BOUNDS.value,
            )
        )
        seeded_db.flush()

        now = datetime.now(tz=UTC)
        for i in range(3):
            po = _make_po(seeded_db, tenant.id, dairy_supplier.id)
            _make_event(
                seeded_db,
                tenant.id,
                po,
                POStatus.PROPOSED.value,
                POStatus.CANCELLED.value,
                OrderBy.OWNER.value,
                created_at=now - timedelta(days=3 - i),
            )

        seeded_db.commit()

        evaluate_demotion(seeded_db, str(tenant.id), str(dairy_supplier.id))

        seeded_db.expire_all()

        cap = seeded_db.scalar(
            select(CapabilityState)
            .where(CapabilityState.tenant_id == tenant.id)
            .where(CapabilityState.supplier_id == dairy_supplier.id)
        )
        assert cap.state == AutonomyState.PROPOSE_ONLY.value

        event = seeded_db.scalar(
            select(AutonomyEvent)
            .where(AutonomyEvent.tenant_id == tenant.id)
            .where(AutonomyEvent.supplier_id == dairy_supplier.id)
            .where(AutonomyEvent.event_type == AutonomyEventType.DEMOTED.value)
        )
        assert event is not None
        assert "rejection streak" in event.reason


class TestDemotionOnLargeEdit:
    def test_demotes_on_edit_over_fifty_percent(
        self, seeded_db, tenant, dairy_supplier
    ):
        seeded_db.add(
            CapabilityState(
                tenant_id=tenant.id,
                supplier_id=dairy_supplier.id,
                state=AutonomyState.AUTO_WITHIN_BOUNDS.value,
            )
        )
        seeded_db.flush()

        po = _make_po(seeded_db, tenant.id, dairy_supplier.id)
        _make_event(
            seeded_db,
            tenant.id,
            po,
            POStatus.PROPOSED.value,
            POStatus.PROPOSED.value,
            OrderBy.OWNER.value,
            edits=[{"item": "Milk", "from": 10.0, "to": 25.0}],
        )
        seeded_db.commit()

        evaluate_demotion(seeded_db, str(tenant.id), str(dairy_supplier.id))

        seeded_db.expire_all()

        cap = seeded_db.scalar(
            select(CapabilityState)
            .where(CapabilityState.tenant_id == tenant.id)
            .where(CapabilityState.supplier_id == dairy_supplier.id)
        )
        assert cap.state == AutonomyState.PROPOSE_ONLY.value

        event = seeded_db.scalar(
            select(AutonomyEvent)
            .where(AutonomyEvent.tenant_id == tenant.id)
            .where(AutonomyEvent.supplier_id == dairy_supplier.id)
            .where(AutonomyEvent.event_type == AutonomyEventType.DEMOTED.value)
        )
        assert event is not None
        assert "max edit" in event.reason


class TestNoDemotionWhenProposeOnly:
    def test_no_demotion_when_already_propose_only(
        self, seeded_db, tenant, dairy_supplier
    ):
        seeded_db.add(
            CapabilityState(
                tenant_id=tenant.id,
                supplier_id=dairy_supplier.id,
                state=AutonomyState.PROPOSE_ONLY.value,
            )
        )
        seeded_db.flush()

        now = datetime.now(tz=UTC)
        for i in range(3):
            po = _make_po(seeded_db, tenant.id, dairy_supplier.id)
            _make_event(
                seeded_db,
                tenant.id,
                po,
                POStatus.PROPOSED.value,
                POStatus.CANCELLED.value,
                OrderBy.OWNER.value,
                created_at=now - timedelta(days=3 - i),
            )

        seeded_db.commit()

        evaluate_demotion(seeded_db, str(tenant.id), str(dairy_supplier.id))

        event = seeded_db.scalar(
            select(AutonomyEvent)
            .where(AutonomyEvent.tenant_id == tenant.id)
            .where(AutonomyEvent.supplier_id == dairy_supplier.id)
            .where(AutonomyEvent.event_type == AutonomyEventType.DEMOTED.value)
        )
        assert event is None
