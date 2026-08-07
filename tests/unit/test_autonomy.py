import json
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import select

from src.consumers.email_consumer import process_events
from src.db.models import (
    AutonomyEvent,
    CapabilityState,
    DecisionLog,
    EmailOutbox,
    InventoryItem,
    ItemDemandForecast,
    POEvent,
    PurchaseOrder,
    SpendLedger,
    Supplier,
    Tenant,
)
from src.schemas.autonomy import AutonomyEventType, AutonomyState
from src.schemas.orders import OrderBy
from src.schemas.suppliers import POStatus
from src.schemas.template import OrderingConfig, TemplateConfig
from src.services.ordering_service import generate_proposals

UTC = datetime.now().astimezone().tzinfo


@pytest.fixture
def tenant(seeded_db):
    return seeded_db.scalar(select(Tenant).limit(1))


@pytest.fixture
def chocolate(seeded_db, tenant):
    return seeded_db.scalar(
        select(InventoryItem)
        .where(InventoryItem.tenant_id == tenant.id)
        .where(InventoryItem.name == "Chocolate Ice Cream")
    )


@pytest.fixture
def dairy_supplier(seeded_db, tenant):
    return seeded_db.scalar(
        select(Supplier)
        .where(Supplier.tenant_id == tenant.id)
        .where(Supplier.name == "Dairy Farms Co-op")
    )


def _seed_forecasts(
    session, tenant_id, item_id, start_date, days, point_estimate, quantile_grid
):
    for i in range(days):
        target = start_date + __import__("datetime").timedelta(days=i)
        session.add(
            ItemDemandForecast(
                tenant_id=tenant_id,
                inventory_item_id=item_id,
                target_date=target,
                point_estimate=point_estimate,
                quantile_grid=quantile_grid,
                model_version="glm_v1",
                as_of_date=start_date,
            )
        )
    session.flush()


class TestDecisionLogParMode:
    def test_par_decision_logged_with_correct_snapshot(
        self, seeded_db, tenant, chocolate
    ):
        result = generate_proposals(seeded_db, tenant.id, [str(chocolate.id)])
        assert len(result) == 1

        logs = seeded_db.scalars(
            select(DecisionLog)
            .where(DecisionLog.purchase_order_id == result[0].id)
            .where(DecisionLog.tenant_id == tenant.id)
        ).all()

        assert len(logs) >= 1
        snapshot = logs[0].snapshot
        assert snapshot["mode"] == "par"
        assert snapshot["target_quantity"] is not None
        assert snapshot["quantile_key"] is None
        assert snapshot["aggregate_demand"] is None
        assert snapshot["quantile_grid"] is None
        assert "quantity_on_hand" in snapshot
        assert "position" in snapshot
        assert "shortfall" in snapshot
        assert "quantity_ordered" in snapshot
        assert "protection_horizon" in snapshot


class TestDecisionLogForecastMode:
    @patch("src.services.ordering_service.horizon.get_now")
    def test_forecast_decision_logged_with_correct_snapshot(
        self, mock_now, seeded_db, tenant, chocolate, dairy_supplier
    ):
        mock_now.return_value = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)
        today = date(2026, 8, 5)
        horizon_days = dairy_supplier.lead_time_days + 2

        _seed_forecasts(
            seeded_db,
            tenant.id,
            chocolate.id,
            start_date=today,
            days=horizon_days,
            point_estimate=Decimal("8.00"),
            quantile_grid={
                "p05": 4.0,
                "p20": 5.5,
                "p50": 7.5,
                "p80": 9.5,
                "p90": 11.0,
                "p95": 12.0,
            },
        )

        result = generate_proposals(seeded_db, tenant.id, [str(chocolate.id)])
        assert len(result) == 1

        logs = seeded_db.scalars(
            select(DecisionLog)
            .where(DecisionLog.purchase_order_id == result[0].id)
            .where(DecisionLog.tenant_id == tenant.id)
        ).all()

        assert len(logs) >= 1
        snapshot = logs[0].snapshot
        assert snapshot["mode"] == "forecast"
        assert snapshot["quantile_key"] == "p95"
        assert snapshot["aggregate_demand"] is not None
        assert snapshot["quantile_grid"] is not None
        assert snapshot["target_quantity"] is None


class TestProposerAutoApproves:
    def test_auto_approves_within_bounds(
        self, seeded_db, tenant, chocolate, dairy_supplier
    ):
        for _ in range(3):
            po = PurchaseOrder(
                tenant_id=tenant.id,
                supplier_id=dairy_supplier.id,
                status=POStatus.SENT.value,
                total_value=Decimal("100.00"),
                created_by=OrderBy.SYSTEM.value,
            )
            seeded_db.add(po)
        seeded_db.flush()

        seeded_db.add(
            CapabilityState(
                tenant_id=tenant.id,
                supplier_id=dairy_supplier.id,
                state=AutonomyState.AUTO_WITHIN_BOUNDS.value,
            )
        )
        seeded_db.flush()

        result = generate_proposals(seeded_db, tenant.id, [str(chocolate.id)])
        assert len(result) == 1
        assert result[0].status == POStatus.APPROVED.value

        events = seeded_db.scalars(
            select(POEvent)
            .where(POEvent.purchase_order_id == result[0].id)
            .where(POEvent.to_status == POStatus.APPROVED.value)
        ).all()
        assert len(events) == 1
        assert events[0].changed_by == OrderBy.SYSTEM.value


class TestProposerRejectsOverBounds:
    def test_stays_proposed_when_over_max_order_value(
        self, seeded_db, tenant, chocolate, dairy_supplier
    ):
        seeded_db.add(
            CapabilityState(
                tenant_id=tenant.id,
                supplier_id=dairy_supplier.id,
                state=AutonomyState.AUTO_WITHIN_BOUNDS.value,
            )
        )
        seeded_db.flush()

        with patch(
            "src.services.ordering_service.autonomy_checks.resolve_config"
        ) as mock_config:
            mock_config.return_value = TemplateConfig(
                ordering=OrderingConfig(max_order_value=1.00)
            )

            result = generate_proposals(seeded_db, tenant.id, [str(chocolate.id)])
            assert len(result) == 1
            assert result[0].status == POStatus.PROPOSED.value


class TestProposerRejectsNovelty:
    def test_stays_proposed_when_no_order_history(
        self, seeded_db, tenant, chocolate, dairy_supplier
    ):
        seeded_db.add(
            CapabilityState(
                tenant_id=tenant.id,
                supplier_id=dairy_supplier.id,
                state=AutonomyState.AUTO_WITHIN_BOUNDS.value,
            )
        )
        seeded_db.flush()

        result = generate_proposals(seeded_db, tenant.id, [str(chocolate.id)])
        assert len(result) == 1
        assert result[0].status == POStatus.PROPOSED.value


class TestExecutorBlocksOutOfBounds:
    def test_executor_rejects_oversized_auto_approved_po(
        self, seeded_db, tenant, chocolate, dairy_supplier
    ):
        result = generate_proposals(seeded_db, tenant.id, [str(chocolate.id)])
        assert len(result) == 1
        po = result[0]

        po.status = POStatus.APPROVED.value
        po.total_value = Decimal("9999.00")
        seeded_db.flush()

        seeded_db.add(
            CapabilityState(
                tenant_id=tenant.id,
                supplier_id=dairy_supplier.id,
                state=AutonomyState.AUTO_WITHIN_BOUNDS.value,
            )
        )
        seeded_db.flush()
        seeded_db.commit()

        fake_event = {
            "id": "test-executor-block",
            "payload": json.dumps(
                {
                    "purchase_order_id": str(po.id),
                    "changed_by": OrderBy.SYSTEM.value,
                }
            ),
            "tenant_id": str(tenant.id),
            "event_type": "po_approved",
        }

        process_events([fake_event])

        seeded_db.expire_all()

        refreshed_po = seeded_db.scalar(
            select(PurchaseOrder).where(PurchaseOrder.id == po.id)
        )
        assert refreshed_po.status == POStatus.PROPOSED.value

        email = seeded_db.scalar(
            select(EmailOutbox)
            .where(EmailOutbox.tenant_id == tenant.id)
            .where(EmailOutbox.idempotency_key == f"po-order-{po.id}")
        )
        assert email is None

        rejection = seeded_db.scalar(
            select(AutonomyEvent)
            .where(AutonomyEvent.tenant_id == tenant.id)
            .where(AutonomyEvent.supplier_id == dairy_supplier.id)
            .where(
                AutonomyEvent.event_type == AutonomyEventType.EXECUTOR_REJECTED.value
            )
        )
        assert rejection is not None

        spend = seeded_db.scalar(
            select(SpendLedger).where(SpendLedger.purchase_order_id == po.id)
        )
        assert spend is None


class TestExecutorAllowsOwnerApproved:
    def test_owner_approved_sends_without_autonomy_checks(
        self, seeded_db, tenant, chocolate
    ):
        result = generate_proposals(seeded_db, tenant.id, [str(chocolate.id)])
        assert len(result) == 1
        po = result[0]

        po.status = POStatus.APPROVED.value
        seeded_db.flush()
        seeded_db.commit()

        fake_event = {
            "id": "test-owner-approve",
            "payload": json.dumps(
                {
                    "purchase_order_id": str(po.id),
                    "changed_by": "owner",
                }
            ),
            "tenant_id": str(tenant.id),
            "event_type": "po_approved",
        }

        process_events([fake_event])

        seeded_db.expire_all()

        email = seeded_db.scalar(
            select(EmailOutbox)
            .where(EmailOutbox.tenant_id == tenant.id)
            .where(EmailOutbox.idempotency_key == f"po-order-{po.id}")
        )
        assert email is not None

        spend = seeded_db.scalar(
            select(SpendLedger).where(SpendLedger.purchase_order_id == po.id)
        )
        assert spend is None
