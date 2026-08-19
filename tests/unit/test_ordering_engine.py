from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import select

from src.db.models import (
    InventoryItem,
    ItemDemandForecast,
    POLine,
    PurchaseOrder,
    Supplier,
    SupplierItem,
    Tenant,
)
from src.schemas.suppliers import POStatus
from src.services.ordering_service import generate_proposals


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


def _seed_forecasts(session, tenant_id, item_id, start_date, days, point_estimate, quantile_grid):
    for i in range(days):
        target = start_date + timedelta(days=i)
        session.add(
            ItemDemandForecast(
                tenant_id=tenant_id,
                inventory_item_id=item_id,
                target_date=target,
                point_estimate=point_estimate,
                quantile_grid=quantile_grid,
                model_version="poisson_glm",
                as_of_date=start_date,
            )
        )
    session.flush()


class TestParFallback:
    """No forecast data exists -- engine falls back to target_quantity - position."""

    def test_uses_par_when_no_forecasts(self, seeded_db, tenant, chocolate):
        result = generate_proposals(seeded_db, tenant.id, [str(chocolate.id)])

        assert len(result) == 1
        line = seeded_db.scalar(
            select(POLine).where(POLine.purchase_order_id == result[0].id)
        )
        # Par logic: target(20) - on_hand(6) = 14, pack_size 5 → ceil(14/5)*5 = 15
        assert line.quantity_ordered == Decimal("15.00")


class TestForecastDrivenOrder:
    """Forecast data exists -- engine uses p95 quantile instead of par."""

    @patch("src.services.ordering_service.horizon.get_now")
    def test_forecast_drives_higher_quantity_than_par(self, mock_now, seeded_db, tenant, chocolate, dairy_supplier):
        mock_now.return_value = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)

        today = date(2026, 8, 5)
        horizon_days = dairy_supplier.lead_time_days + 2  # cover the full horizon

        _seed_forecasts(
            seeded_db, tenant.id, chocolate.id,
            start_date=today,
            days=horizon_days,
            point_estimate=Decimal("8.00"),
            quantile_grid={
                "p05": 4.0, "p20": 5.5, "p50": 7.5,
                "p80": 9.5, "p90": 11.0, "p95": 12.0,
            },
        )

        result = generate_proposals(seeded_db, tenant.id, [str(chocolate.id)])

        assert len(result) == 1
        line = seeded_db.scalar(
            select(POLine).where(POLine.purchase_order_id == result[0].id)
        )
        # p95 summed over horizon > par target, so quantity should exceed par-based 15
        assert line.quantity_ordered > Decimal("15.00")


class TestHeatWaveRaisesQuantity:
    """Higher forecast demand → higher order quantity."""

    @patch("src.services.ordering_service.horizon.get_now")
    def test_high_demand_forecast_increases_order(self, mock_now, seeded_db, tenant, chocolate, dairy_supplier):
        mock_now.return_value = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)

        today = date(2026, 8, 5)
        horizon_days = dairy_supplier.lead_time_days + 2

        # Normal demand
        _seed_forecasts(
            seeded_db, tenant.id, chocolate.id,
            start_date=today,
            days=horizon_days,
            point_estimate=Decimal("5.00"),
            quantile_grid={
                "p05": 2.0, "p20": 3.0, "p50": 4.5,
                "p80": 6.0, "p90": 7.0, "p95": 8.0,
            },
        )

        normal_result = generate_proposals(seeded_db, tenant.id, [str(chocolate.id)])
        assert len(normal_result) == 1
        normal_line = seeded_db.scalar(
            select(POLine).where(POLine.purchase_order_id == normal_result[0].id)
        )
        normal_qty = normal_line.quantity_ordered

        # Clean up for second run
        normal_result[0].status = POStatus.CANCELLED.value
        seeded_db.flush()

        # Delete old forecasts
        seeded_db.execute(
            select(ItemDemandForecast)
            .where(ItemDemandForecast.inventory_item_id == chocolate.id)
        )
        for fc in seeded_db.scalars(
            select(ItemDemandForecast)
            .where(ItemDemandForecast.inventory_item_id == chocolate.id)
        ).all():
            seeded_db.delete(fc)
        seeded_db.flush()

        # Heat wave demand -- much higher
        _seed_forecasts(
            seeded_db, tenant.id, chocolate.id,
            start_date=today,
            days=horizon_days,
            point_estimate=Decimal("15.00"),
            quantile_grid={
                "p05": 8.0, "p20": 10.0, "p50": 14.0,
                "p80": 18.0, "p90": 20.0, "p95": 22.0,
            },
        )

        heat_result = generate_proposals(seeded_db, tenant.id, [str(chocolate.id)])
        assert len(heat_result) == 1
        heat_line = seeded_db.scalar(
            select(POLine).where(POLine.purchase_order_id == heat_result[0].id)
        )

        assert heat_line.quantity_ordered > normal_qty


class TestWidenedIntervalsRaiseSafetyStock:
    """Wider prediction intervals → higher p95 → more safety stock."""

    @patch("src.services.ordering_service.horizon.get_now")
    def test_uncertain_forecast_orders_more(self, mock_now, seeded_db, tenant, chocolate, dairy_supplier):
        mock_now.return_value = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)

        today = date(2026, 8, 5)
        horizon_days = dairy_supplier.lead_time_days + 2

        # Tight intervals
        _seed_forecasts(
            seeded_db, tenant.id, chocolate.id,
            start_date=today,
            days=horizon_days,
            point_estimate=Decimal("8.00"),
            quantile_grid={
                "p05": 7.0, "p20": 7.5, "p50": 8.0,
                "p80": 8.5, "p90": 9.0, "p95": 9.5,
            },
        )

        tight_result = generate_proposals(seeded_db, tenant.id, [str(chocolate.id)])
        assert len(tight_result) == 1
        tight_line = seeded_db.scalar(
            select(POLine).where(POLine.purchase_order_id == tight_result[0].id)
        )
        tight_qty = tight_line.quantity_ordered

        # Clean up
        tight_result[0].status = POStatus.CANCELLED.value
        for fc in seeded_db.scalars(
            select(ItemDemandForecast)
            .where(ItemDemandForecast.inventory_item_id == chocolate.id)
        ).all():
            seeded_db.delete(fc)
        seeded_db.flush()

        # Wide intervals -- same point estimate, much wider spread
        _seed_forecasts(
            seeded_db, tenant.id, chocolate.id,
            start_date=today,
            days=horizon_days,
            point_estimate=Decimal("8.00"),
            quantile_grid={
                "p05": 2.0, "p20": 4.0, "p50": 8.0,
                "p80": 12.0, "p90": 16.0, "p95": 20.0,
            },
        )

        wide_result = generate_proposals(seeded_db, tenant.id, [str(chocolate.id)])
        assert len(wide_result) == 1
        wide_line = seeded_db.scalar(
            select(POLine).where(POLine.purchase_order_id == wide_result[0].id)
        )

        assert wide_line.quantity_ordered > tight_qty


class TestOnOrderNetting:
    """Stock in transit reduces the order quantity."""

    @patch("src.services.ordering_service.horizon.get_now")
    def test_confirmed_po_reduces_new_order(self, mock_now, seeded_db, tenant, chocolate, dairy_supplier):
        mock_now.return_value = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)

        today = date(2026, 8, 5)
        horizon_days = dairy_supplier.lead_time_days + 2

        _seed_forecasts(
            seeded_db, tenant.id, chocolate.id,
            start_date=today,
            days=horizon_days,
            point_estimate=Decimal("8.00"),
            quantile_grid={
                "p05": 4.0, "p20": 5.5, "p50": 7.5,
                "p80": 9.5, "p90": 11.0, "p95": 12.0,
            },
        )

        # First run -- no on-order
        result_no_oo = generate_proposals(seeded_db, tenant.id, [str(chocolate.id)])
        assert len(result_no_oo) == 1
        line_no_oo = seeded_db.scalar(
            select(POLine).where(POLine.purchase_order_id == result_no_oo[0].id)
        )
        qty_no_oo = line_no_oo.quantity_ordered

        # Cancel so engine can re-evaluate
        result_no_oo[0].status = POStatus.CANCELLED.value
        seeded_db.flush()

        # Create a confirmed PO with some stock on the way
        supplier_item = seeded_db.scalar(
            select(SupplierItem)
            .where(SupplierItem.supplier_id == dairy_supplier.id)
            .where(SupplierItem.inventory_item_id == chocolate.id)
        )
        existing_po = PurchaseOrder(
            tenant_id=tenant.id,
            supplier_id=dairy_supplier.id,
            status=POStatus.CONFIRMED.value,
            total_value=Decimal("45.00"),
            created_by="owner",
            expected_delivery=today + timedelta(days=2),
        )
        seeded_db.add(existing_po)
        seeded_db.flush()

        seeded_db.add(
            POLine(
                tenant_id=tenant.id,
                purchase_order_id=existing_po.id,
                inventory_item_id=chocolate.id,
                supplier_item_id=supplier_item.id,
                quantity_ordered=Decimal("10.00"),
                unit_cost=Decimal("4.50"),
            )
        )
        seeded_db.flush()

        # Second run -- 10 units on order
        result_with_oo = generate_proposals(seeded_db, tenant.id, [str(chocolate.id)])

        if len(result_with_oo) == 0:
            # On-order covered the shortfall entirely -- correct behavior
            assert True
        else:
            line_with_oo = seeded_db.scalar(
                select(POLine).where(POLine.purchase_order_id == result_with_oo[0].id)
            )
            assert line_with_oo.quantity_ordered < qty_no_oo