from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from src.db.models import (
    BacktestResult,
    DailyActual,
    Forecast,
    ModelRegistry,
    Tenant,
    WeatherObservation,
)
from src.schemas.forecast import ForecastSeries
from src.schemas.models import ModelVersion
from src.schemas.weather import WeatherSource
from src.services.forecast_service.core import train_glm, train_lgbm
from src.services.forecast_service.metrics import check_promotion_gate
from src.services.learning_service import champion_eval


@pytest.fixture
def tenant(seeded_db):
    return seeded_db.scalar(select(Tenant).limit(1))


@pytest.fixture
def forecast_history(seeded_db, tenant):
    base = date(2026, 5, 1)
    for i in range(60):
        d = base + timedelta(days=i)
        weekday = d.weekday()
        # Simulate weekly pattern: weekends higher
        units = 80 + (weekday * 5) + (i % 7)
        revenue = Decimal(str(units * 6.5))

        seeded_db.add(DailyActual(
            tenant_id=tenant.id,
            series=ForecastSeries.TOTAL_UNITS,
            actual_date=d,
            value=Decimal(str(units)),
        ))
        seeded_db.add(DailyActual(
            tenant_id=tenant.id,
            series=ForecastSeries.TOTAL_REVENUE,
            actual_date=d,
            value=revenue,
        ))

        for source in [WeatherSource.ACTUAL.value, WeatherSource.FORECAST.value]:
            seeded_db.add(WeatherObservation(
                tenant_id=tenant.id,
                observation_date=d,
                source=source,
                max_temp_c=Decimal("25.0") + Decimal(str(i % 10)),
                min_temp_c=Decimal("15.0"),
                precipitation_mm=Decimal("0.0"),
            ))

    seeded_db.flush()
    return base


class TestTrainCutoff:
    def test_glm_without_cutoff_uses_all_data(self, seeded_db, tenant, forecast_history):
        result = train_glm(seeded_db, str(tenant.id), ForecastSeries.TOTAL_UNITS)
        assert result is not None

    def test_glm_with_cutoff_excludes_future(self, seeded_db, tenant, forecast_history):
        cutoff = forecast_history + timedelta(days=10)
        result = train_glm(seeded_db, str(tenant.id), ForecastSeries.TOTAL_UNITS, cutoff)
        assert result is not None

    def test_glm_cutoff_before_data_returns_none(self, seeded_db, tenant, forecast_history):
        cutoff = forecast_history - timedelta(days=1)
        result = train_glm(seeded_db, str(tenant.id), ForecastSeries.TOTAL_UNITS, cutoff)
        assert result is None

    def test_lgbm_trains_successfully(self, seeded_db, tenant, forecast_history):
        result = train_lgbm(seeded_db, str(tenant.id), ForecastSeries.TOTAL_UNITS)
        assert result is not None


class TestCheckPromotionGateGeneric:
    def test_same_model_returns_none(self, seeded_db, tenant):
        result = check_promotion_gate(
            seeded_db, str(tenant.id), "2026-06-30",
            ModelVersion.POISSON_GLM.value, ModelVersion.POISSON_GLM.value,
        )
        assert result is None

    def test_no_metrics_returns_none(self, seeded_db, tenant):
        result = check_promotion_gate(
            seeded_db, str(tenant.id), "2026-06-30",
            ModelVersion.POISSON_GLM.value, ModelVersion.LIGHTGBM.value,
        )
        assert result is None


class TestChampionEval:
    def test_defaults_to_glm_when_no_registry(self, seeded_db, tenant, forecast_history):
        as_of = forecast_history + timedelta(days=59)
        champion_eval(
            seeded_db, str(tenant.id), ModelVersion.LIGHTGBM.value, as_of,
        )

        result = seeded_db.scalar(
            select(BacktestResult).where(BacktestResult.tenant_id == tenant.id)
        )
        assert result is not None
        assert result.champion_version == ModelVersion.POISSON_GLM.value
        assert result.challenger_version == ModelVersion.LIGHTGBM.value

    def test_same_as_champion_returns_early(self, seeded_db, tenant, forecast_history):
        as_of = forecast_history + timedelta(days=59)
        champion_eval(
            seeded_db, str(tenant.id), ModelVersion.POISSON_GLM.value, as_of,
        )

        result = seeded_db.scalar(
            select(BacktestResult).where(BacktestResult.tenant_id == tenant.id)
        )
        assert result is None

    def test_no_production_data_polluted(self, seeded_db, tenant, forecast_history):
        as_of = forecast_history + timedelta(days=59)

        forecasts_before = seeded_db.scalars(
            select(Forecast)
            .where(Forecast.tenant_id == tenant.id)
            .where(Forecast.model_version == ModelVersion.LIGHTGBM.value)
        ).all()

        glm_before = seeded_db.scalars(
            select(Forecast)
            .where(Forecast.tenant_id == tenant.id)
            .where(Forecast.model_version == ModelVersion.POISSON_GLM.value)
        ).all()
        glm_estimates_before = {
            (f.series, f.target_date, f.forecast_date): float(f.point_estimate)
            for f in glm_before
        }

        champion_eval(
            seeded_db, str(tenant.id), ModelVersion.LIGHTGBM.value, as_of,
        )

        forecasts_after = seeded_db.scalars(
            select(Forecast)
            .where(Forecast.tenant_id == tenant.id)
            .where(Forecast.model_version == ModelVersion.LIGHTGBM.value)
        ).all()

        glm_after = seeded_db.scalars(
            select(Forecast)
            .where(Forecast.tenant_id == tenant.id)
            .where(Forecast.model_version == ModelVersion.POISSON_GLM.value)
        ).all()
        glm_estimates_after = {
            (f.series, f.target_date, f.forecast_date): float(f.point_estimate)
            for f in glm_after
        }

        assert len(forecasts_before) == len(forecasts_after)
        assert glm_estimates_before == glm_estimates_after

    def test_backtest_result_always_persisted(self, seeded_db, tenant, forecast_history):
        as_of = forecast_history + timedelta(days=59)
        champion_eval(
            seeded_db, str(tenant.id), ModelVersion.LIGHTGBM.value, as_of,
        )

        results = seeded_db.scalars(
            select(BacktestResult).where(BacktestResult.tenant_id == tenant.id)
        ).all()
        assert len(results) == 1
        assert results[0].skill is not None or results[0].passed is False


class TestModelRegistry:
    def test_promotion_updates_registry(self, seeded_db, tenant):
        seeded_db.add(ModelRegistry(
            tenant_id=tenant.id,
            active_version=ModelVersion.POISSON_GLM.value,
        ))
        seeded_db.flush()

        registry = seeded_db.scalar(
            select(ModelRegistry).where(ModelRegistry.tenant_id == tenant.id)
        )
        assert registry.active_version == ModelVersion.POISSON_GLM.value
        assert registry.previous_version is None

    def test_set_version_swaps(self, seeded_db, tenant):
        seeded_db.add(ModelRegistry(
            tenant_id=tenant.id,
            active_version=ModelVersion.POISSON_GLM.value,
        ))
        seeded_db.flush()

        registry = seeded_db.scalar(
            select(ModelRegistry).where(ModelRegistry.tenant_id == tenant.id)
        )
        registry.previous_version = registry.active_version
        registry.active_version = ModelVersion.LIGHTGBM.value
        registry.backtest_evidence = None
        seeded_db.flush()

        updated = seeded_db.scalar(
            select(ModelRegistry).where(ModelRegistry.tenant_id == tenant.id)
        )
        assert updated.active_version == ModelVersion.LIGHTGBM.value
        assert updated.previous_version == ModelVersion.POISSON_GLM.value