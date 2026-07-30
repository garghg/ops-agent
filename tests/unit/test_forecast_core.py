from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from src.db.models import (
    DailyActual,
    Forecast,
    SaleLineItem,
    SaleTransaction,
    Tenant,
)
from src.services.forecast_service import (
    actuals_aggregate,
    forecast_seasonal_naive,
    forecast_trailing_mean,
)


@pytest.fixture
def tenant(seeded_db):
    return seeded_db.scalar(select(Tenant).limit(1))


class TestActualsAggregate:
    def test_computes_revenue_and_units(self, seeded_db, tenant):
        txn1 = SaleTransaction(
            external_transaction_id="agg-001",
            source="synthetic",
            timestamp=datetime.fromisoformat("2026-07-28T14:00:00+00:00"),
            total=Decimal("25.00"),
            payment_method="card",
            transaction_type="sale",
            tenant_id=tenant.id,
        )
        txn2 = SaleTransaction(
            external_transaction_id="agg-002",
            source="synthetic",
            timestamp=datetime.fromisoformat("2026-07-28T15:00:00+00:00"),
            total=Decimal("15.00"),
            payment_method="card",
            transaction_type="sale",
            tenant_id=tenant.id,
        )
        seeded_db.add_all([txn1, txn2])
        seeded_db.flush()

        seeded_db.add_all(
            [
                SaleLineItem(
                    sale_transaction_id=txn1.id,
                    item_name="Single Scoop",
                    quantity=2,
                    unit_price=Decimal("12.50"),
                    tenant_id=tenant.id,
                ),
                SaleLineItem(
                    sale_transaction_id=txn2.id,
                    item_name="Double Scoop",
                    quantity=1,
                    unit_price=Decimal("15.00"),
                    tenant_id=tenant.id,
                ),
            ]
        )
        seeded_db.commit()

        actuals_aggregate(seeded_db, str(tenant.id), "2026-07-28")

        revenue = seeded_db.scalar(
            select(DailyActual.value).where(
                DailyActual.tenant_id == tenant.id,
                DailyActual.series == "total_revenue",
                DailyActual.actual_date == date(2026, 7, 28),
            )
        )
        units = seeded_db.scalar(
            select(DailyActual.value).where(
                DailyActual.tenant_id == tenant.id,
                DailyActual.series == "total_units",
                DailyActual.actual_date == date(2026, 7, 28),
            )
        )
        assert revenue == Decimal("40.00")
        assert units == 3

    def test_idempotent(self, seeded_db, tenant):
        seeded_db.add(
            SaleTransaction(
                external_transaction_id="agg-idem-001",
                source="synthetic",
                timestamp=datetime.fromisoformat("2026-07-27T12:00:00+00:00"),
                total=Decimal("10.00"),
                payment_method="card",
                transaction_type="sale",
                tenant_id=tenant.id,
            )
        )
        seeded_db.commit()

        actuals_aggregate(seeded_db, str(tenant.id), "2026-07-27")
        actuals_aggregate(seeded_db, str(tenant.id), "2026-07-27")

        count = len(
            seeded_db.scalars(
                select(DailyActual).where(
                    DailyActual.tenant_id == tenant.id,
                    DailyActual.actual_date == date(2026, 7, 27),
                )
            ).all()
        )
        assert count == 2  # total_revenue + total_units, not duplicated


class TestForecastSeasonalNaive:
    def test_averages_same_weekday(self, seeded_db, tenant):
        # Seed 4 Mondays of data
        mondays = [
            date(2026, 6, 29),
            date(2026, 7, 6),
            date(2026, 7, 13),
            date(2026, 7, 20),
        ]
        values = [Decimal(100), Decimal(120), Decimal(140), Decimal(160)]
        for d, v in zip(mondays, values):
            seeded_db.add(
                DailyActual(
                    tenant_id=tenant.id,
                    series="total_revenue",
                    actual_date=d,
                    value=v,
                )
            )
        seeded_db.commit()

        forecast_seasonal_naive(seeded_db, str(tenant.id), "2026-07-21")

        # Next Monday is 2026-07-27 (offset=6)
        forecast = seeded_db.scalar(
            select(Forecast).where(
                Forecast.tenant_id == tenant.id,
                Forecast.series == "total_revenue",
                Forecast.target_date == date(2026, 7, 27),
                Forecast.model_version == "seasonal_naive",
            )
        )
        assert forecast is not None
        # Only July 6, 13, 20 are within 28-day lookback from July 21
        expected = (Decimal(100) + Decimal(120) + Decimal(140) + Decimal(160)) / 4
        assert forecast.point_estimate == pytest.approx(expected, abs=Decimal("0.01"))


class TestForecastTrailingMean:
    def test_averages_last_7_days(self, seeded_db, tenant):
        base = date(2026, 7, 21)
        for i in range(7):
            seeded_db.add(
                DailyActual(
                    tenant_id=tenant.id,
                    series="total_units",
                    actual_date=base - timedelta(days=7 - i),
                    value=Decimal(str(50 + i * 10)),  # 50, 60, 70, 80, 90, 100, 110
                )
            )
        seeded_db.commit()

        forecast_trailing_mean(seeded_db, str(tenant.id), "2026-07-21")

        forecast = seeded_db.scalar(
            select(Forecast).where(
                Forecast.tenant_id == tenant.id,
                Forecast.series == "total_units",
                Forecast.target_date == date(2026, 7, 22),
                Forecast.model_version == "trailing_7d_mean",
            )
        )
        assert forecast is not None
        expected = Decimal(80)  # mean of 50...110
        assert forecast.point_estimate == pytest.approx(expected, abs=Decimal("0.01"))

