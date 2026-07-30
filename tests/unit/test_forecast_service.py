from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from src.db.models import (
    DailyActual,
    Forecast,
    ForecastMetric,
    SaleLineItem,
    SaleTransaction,
    Tenant,
)
from src.services.forecast_service import (
    actuals_aggregate,
    check_promotion_gate,
    compute_forecast_metrics,
    compute_quantile_grid,
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


class TestComputeForecastMetrics:
    def test_basic_mae_and_bias(self, seeded_db, tenant):
        seeded_db.add(
            Forecast(
                tenant_id=tenant.id,
                series="total_revenue",
                target_date=date(2026, 7, 28),
                model_version="seasonal_naive",
                point_estimate=Decimal("80.00"),
                forecast_date=date(2026, 7, 27),
            )
        )
        seeded_db.add(
            DailyActual(
                tenant_id=tenant.id,
                series="total_revenue",
                actual_date=date(2026, 7, 28),
                value=Decimal("100.00"),
            )
        )
        seeded_db.commit()

        compute_forecast_metrics(seeded_db, str(tenant.id), "2026-07-28")

        metric = seeded_db.scalar(
            select(ForecastMetric).where(
                ForecastMetric.tenant_id == tenant.id,
                ForecastMetric.series == "total_revenue",
                ForecastMetric.target_date == date(2026, 7, 28),
            )
        )
        assert metric.mae == Decimal("20.00")
        assert metric.bias == Decimal("-20.00")

    def test_coverage_true(self, seeded_db, tenant):
        seeded_db.add(
            Forecast(
                tenant_id=tenant.id,
                series="total_units",
                target_date=date(2026, 7, 25),
                model_version="seasonal_naive",
                point_estimate=Decimal("90.00"),
                quantile_grid={"p05": 50, "p95": 120},
                forecast_date=date(2026, 7, 24),
            )
        )
        seeded_db.add(
            DailyActual(
                tenant_id=tenant.id,
                series="total_units",
                actual_date=date(2026, 7, 25),
                value=Decimal("100.00"),
            )
        )
        seeded_db.commit()

        compute_forecast_metrics(seeded_db, str(tenant.id), "2026-07-25")

        metric = seeded_db.scalar(
            select(ForecastMetric).where(
                ForecastMetric.tenant_id == tenant.id,
                ForecastMetric.target_date == date(2026, 7, 25),
            )
        )
        assert metric.coverage is True

    def test_coverage_false(self, seeded_db, tenant):
        seeded_db.add(
            Forecast(
                tenant_id=tenant.id,
                series="total_units",
                target_date=date(2026, 7, 24),
                model_version="seasonal_naive",
                point_estimate=Decimal("90.00"),
                quantile_grid={"p05": 50, "p95": 80},
                forecast_date=date(2026, 7, 23),
            )
        )
        seeded_db.add(
            DailyActual(
                tenant_id=tenant.id,
                series="total_units",
                actual_date=date(2026, 7, 24),
                value=Decimal("100.00"),
            )
        )
        seeded_db.commit()

        compute_forecast_metrics(seeded_db, str(tenant.id), "2026-07-24")

        metric = seeded_db.scalar(
            select(ForecastMetric).where(
                ForecastMetric.tenant_id == tenant.id,
                ForecastMetric.target_date == date(2026, 7, 24),
            )
        )
        assert metric.coverage is False

    def test_coverage_none_without_grid(self, seeded_db, tenant):
        seeded_db.add(
            Forecast(
                tenant_id=tenant.id,
                series="total_revenue",
                target_date=date(2026, 7, 23),
                model_version="trailing_7d_mean",
                point_estimate=Decimal("90.00"),
                forecast_date=date(2026, 7, 22),
            )
        )
        seeded_db.add(
            DailyActual(
                tenant_id=tenant.id,
                series="total_revenue",
                actual_date=date(2026, 7, 23),
                value=Decimal("100.00"),
            )
        )
        seeded_db.commit()

        compute_forecast_metrics(seeded_db, str(tenant.id), "2026-07-23")

        metric = seeded_db.scalar(
            select(ForecastMetric).where(
                ForecastMetric.tenant_id == tenant.id,
                ForecastMetric.target_date == date(2026, 7, 23),
            )
        )
        assert metric.coverage is None

    def test_no_actual_skips_metric(self, seeded_db, tenant):
        seeded_db.add(
            Forecast(
                tenant_id=tenant.id,
                series="total_revenue",
                target_date=date(2026, 7, 22),
                model_version="seasonal_naive",
                point_estimate=Decimal("80.00"),
                forecast_date=date(2026, 7, 21),
            )
        )
        seeded_db.commit()

        compute_forecast_metrics(seeded_db, str(tenant.id), "2026-07-22")

        metric = seeded_db.scalar(
            select(ForecastMetric).where(
                ForecastMetric.tenant_id == tenant.id,
                ForecastMetric.target_date == date(2026, 7, 22),
            )
        )
        assert metric is None


class TestComputeQuantileGrid:
    def test_returns_buckets_with_enough_data(self, seeded_db, tenant):
        base = date(2026, 6, 1)
        # Seed 15 short (horizon 1-3), 15 medium (4-7), 15 long (8-14)
        for i in range(15):
            forecast_date = base + timedelta(days=i)
            # short: horizon 2
            seeded_db.add(
                ForecastMetric(
                    tenant_id=tenant.id,
                    series="total_units",
                    target_date=forecast_date + timedelta(days=2),
                    forecast_date=forecast_date,
                    model_version="poisson_glm",
                    mae=Decimal("5.00"),
                    bias=Decimal("5.00"),
                    coverage=True,
                )
            )
            # medium: horizon 5
            seeded_db.add(
                ForecastMetric(
                    tenant_id=tenant.id,
                    series="total_units",
                    target_date=forecast_date + timedelta(days=5),
                    forecast_date=forecast_date,
                    model_version="poisson_glm",
                    mae=Decimal("10.00"),
                    bias=Decimal("10.00"),
                    coverage=True,
                )
            )
            # long: horizon 10
            seeded_db.add(
                ForecastMetric(
                    tenant_id=tenant.id,
                    series="total_units",
                    target_date=forecast_date + timedelta(days=10),
                    forecast_date=forecast_date,
                    model_version="poisson_glm",
                    mae=Decimal("20.00"),
                    bias=Decimal("20.00"),
                    coverage=True,
                )
            )
        seeded_db.commit()

        result = compute_quantile_grid(
            seeded_db, str(tenant.id), "poisson_glm", "2026-07-20"
        )

        assert result is not None
        assert "short" in result
        assert "medium" in result
        assert "long" in result
        for bucket in result.values():
            assert set(bucket.keys()) == {"p05", "p20", "p50", "p80", "p90", "p95"}
        # All short biases are 5.0, so every quantile should be 5.0
        assert result["short"]["p50"] == pytest.approx(5.0)
        assert result["medium"]["p50"] == pytest.approx(10.0)
        assert result["long"]["p50"] == pytest.approx(20.0)

    def test_excludes_bucket_below_minimum(self, seeded_db, tenant):
        base = date(2026, 6, 1)
        # 12 short-horizon metrics (enough)
        for i in range(12):
            forecast_date = base + timedelta(days=i)
            seeded_db.add(
                ForecastMetric(
                    tenant_id=tenant.id,
                    series="total_units",
                    target_date=forecast_date + timedelta(days=1),
                    forecast_date=forecast_date,
                    model_version="poisson_glm",
                    mae=Decimal("5.00"),
                    bias=Decimal("5.00"),
                    coverage=True,
                )
            )
        # Only 3 medium-horizon metrics (not enough)
        for i in range(3):
            forecast_date = base + timedelta(days=i)
            seeded_db.add(
                ForecastMetric(
                    tenant_id=tenant.id,
                    series="total_units",
                    target_date=forecast_date + timedelta(days=6),
                    forecast_date=forecast_date,
                    model_version="poisson_glm",
                    mae=Decimal("10.00"),
                    bias=Decimal("10.00"),
                    coverage=True,
                )
            )
        seeded_db.commit()

        result = compute_quantile_grid(
            seeded_db, str(tenant.id), "poisson_glm", "2026-07-20"
        )

        assert result is not None
        assert "short" in result
        assert "medium" not in result
        assert "long" not in result

    def test_returns_none_with_no_data(self, seeded_db, tenant):
        result = compute_quantile_grid(
            seeded_db, str(tenant.id), "poisson_glm", "2026-07-20"
        )
        assert result is None


class TestCheckPromotionGate:
    def _seed_metrics(self, seeded_db, tenant, glm_mae, naive_mae, coverage_values):
        base = date(2026, 6, 22)  # 28 days before July 20
        for i in range(28):
            target = base + timedelta(days=i)
            forecast_dt = target - timedelta(days=1)

            seeded_db.add(
                DailyActual(
                    tenant_id=tenant.id,
                    series="total_units",
                    actual_date=target,
                    value=Decimal("100.00"),
                )
            )
            seeded_db.add(
                ForecastMetric(
                    tenant_id=tenant.id,
                    series="total_units",
                    target_date=target,
                    forecast_date=forecast_dt,
                    model_version="poisson_glm",
                    mae=Decimal(str(glm_mae)),
                    bias=Decimal(str(glm_mae)),
                    coverage=coverage_values[i] if i < len(coverage_values) else None,
                )
            )
            seeded_db.add(
                ForecastMetric(
                    tenant_id=tenant.id,
                    series="total_units",
                    target_date=target,
                    forecast_date=forecast_dt,
                    model_version="seasonal_naive",
                    mae=Decimal(str(naive_mae)),
                    bias=Decimal(str(naive_mae)),
                    coverage=None,
                )
            )
        seeded_db.commit()

    def test_passes_when_glm_beats_naive_and_coverage_in_range(self, seeded_db, tenant):
        # GLM MAE=5, naive MAE=15 → skill > 0
        # 25 out of 28 coverage True → ~89% in [80, 98]
        coverage = [True] * 25 + [False] * 3
        self._seed_metrics(
            seeded_db, tenant, glm_mae=5, naive_mae=15, coverage_values=coverage
        )

        result = check_promotion_gate(seeded_db, str(tenant.id), "2026-07-20")

        assert result is not None
        assert result["passed"] is True
        assert result["skills"] > 0
        assert 80 <= result["coverage_pct"] <= 98

    def test_fails_when_skill_negative(self, seeded_db, tenant):
        # GLM MAE=20, naive MAE=10 → skill < 0
        coverage = [True] * 25 + [False] * 3
        self._seed_metrics(
            seeded_db, tenant, glm_mae=20, naive_mae=10, coverage_values=coverage
        )

        result = check_promotion_gate(seeded_db, str(tenant.id), "2026-07-20")

        assert result is not None
        assert result["passed"] is False
        assert result["skills"] < 0

    def test_fails_when_coverage_too_low(self, seeded_db, tenant):
        # GLM beats naive but coverage too low
        # 15 out of 28 True → ~54%, below 80
        coverage = [True] * 15 + [False] * 13
        self._seed_metrics(
            seeded_db, tenant, glm_mae=5, naive_mae=15, coverage_values=coverage
        )

        result = check_promotion_gate(seeded_db, str(tenant.id), "2026-07-20")

        assert result is not None
        assert result["passed"] is False
        assert result["coverage_pct"] < 80

    def test_returns_none_with_no_data(self, seeded_db, tenant):
        result = check_promotion_gate(seeded_db, str(tenant.id), "2026-07-20")
        assert result is None
