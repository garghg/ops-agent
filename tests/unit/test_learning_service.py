from datetime import date, timedelta

import pytest
from sqlalchemy import select

from src.db.models import Anomaly, CorrectionFactor, FactorHistory, Tenant
from src.schemas.anomaly import AnomalyType
from src.schemas.learning import FactorKind
from src.services.learning_service import get_factor, reset_factor, update_factor


@pytest.fixture
def tenant(seeded_db):
    return seeded_db.scalar(select(Tenant).limit(1))


@pytest.fixture
def business_date():
    return date(2026, 8, 5)


class TestFirstObservation:
    def test_sets_value_directly(self, seeded_db, tenant, business_date):
        update_factor(
            seeded_db,
            str(tenant.id),
            FactorKind.FORECAST_BIAS,
            "total_units",
            1.2,
            half_life=7,
            clamp_low=0.75,
            clamp_high=1.30,
            business_date=business_date,
        )
        factor = seeded_db.scalar(
            select(CorrectionFactor)
            .where(CorrectionFactor.tenant_id == tenant.id)
            .where(CorrectionFactor.kind == FactorKind.FORECAST_BIAS)
            .where(CorrectionFactor.scope_key == "total_units")
        )
        assert float(factor.value) == pytest.approx(1.2, abs=0.001)
        assert factor.evidence_count == 1

    def test_first_observation_clamped(self, seeded_db, tenant, business_date):
        update_factor(
            seeded_db,
            str(tenant.id),
            FactorKind.FORECAST_BIAS,
            "total_units",
            2.0,
            half_life=7,
            clamp_low=0.75,
            clamp_high=1.30,
            business_date=business_date,
        )
        factor = seeded_db.scalar(
            select(CorrectionFactor)
            .where(CorrectionFactor.tenant_id == tenant.id)
            .where(CorrectionFactor.kind == FactorKind.FORECAST_BIAS)
        )
        assert float(factor.value) == pytest.approx(1.30, abs=0.001)
        assert factor.consecutive_clamps == 1


class TestEWMA:
    def test_blends_toward_observation(self, seeded_db, tenant, business_date):
        update_factor(
            seeded_db,
            str(tenant.id),
            FactorKind.FORECAST_BIAS,
            "total_units",
            1.0,
            half_life=7,
            clamp_low=0.75,
            clamp_high=1.30,
            business_date=business_date,
        )
        update_factor(
            seeded_db,
            str(tenant.id),
            FactorKind.FORECAST_BIAS,
            "total_units",
            1.2,
            half_life=7,
            clamp_low=0.75,
            clamp_high=1.30,
            business_date=business_date + timedelta(days=1),
        )
        factor = seeded_db.scalar(
            select(CorrectionFactor)
            .where(CorrectionFactor.tenant_id == tenant.id)
            .where(CorrectionFactor.kind == FactorKind.FORECAST_BIAS)
        )
        assert 1.0 < float(factor.value) < 1.2
        assert factor.evidence_count == 2

    def test_repeated_observations_converge(self, seeded_db, tenant, business_date):
        update_factor(
            seeded_db,
            str(tenant.id),
            FactorKind.FORECAST_BIAS,
            "total_units",
            1.0,
            half_life=7,
            clamp_low=0.75,
            clamp_high=1.30,
            business_date=business_date,
        )
        for i in range(1, 15):
            update_factor(
                seeded_db,
                str(tenant.id),
                FactorKind.FORECAST_BIAS,
                "total_units",
                1.2,
                half_life=7,
                clamp_low=0.75,
                clamp_high=1.30,
                business_date=business_date + timedelta(days=i),
            )
        factor = seeded_db.scalar(
            select(CorrectionFactor)
            .where(CorrectionFactor.tenant_id == tenant.id)
            .where(CorrectionFactor.kind == FactorKind.FORECAST_BIAS)
        )
        assert float(factor.value) == pytest.approx(1.2, abs=0.06)
        assert factor.evidence_count == 15


class TestClamping:
    def test_clamps_low(self, seeded_db, tenant, business_date):
        update_factor(
            seeded_db,
            str(tenant.id),
            FactorKind.FORECAST_BIAS,
            "total_units",
            0.5,
            half_life=7,
            clamp_low=0.75,
            clamp_high=1.30,
            business_date=business_date,
        )
        factor = seeded_db.scalar(
            select(CorrectionFactor)
            .where(CorrectionFactor.tenant_id == tenant.id)
            .where(CorrectionFactor.kind == FactorKind.FORECAST_BIAS)
        )
        assert float(factor.value) == pytest.approx(0.75, abs=0.001)
        assert factor.consecutive_clamps == 1

        history = seeded_db.scalar(
            select(FactorHistory).where(FactorHistory.correction_factor_id == factor.id)
        )
        assert history.clamped is True

    def test_clamps_high(self, seeded_db, tenant, business_date):
        update_factor(
            seeded_db,
            str(tenant.id),
            FactorKind.FORECAST_BIAS,
            "total_units",
            1.5,
            half_life=7,
            clamp_low=0.75,
            clamp_high=1.30,
            business_date=business_date,
        )
        factor = seeded_db.scalar(
            select(CorrectionFactor)
            .where(CorrectionFactor.tenant_id == tenant.id)
            .where(CorrectionFactor.kind == FactorKind.FORECAST_BIAS)
        )
        assert float(factor.value) == pytest.approx(1.30, abs=0.001)
        assert factor.consecutive_clamps == 1

    def test_clamp_resets_on_normal_observation(self, seeded_db, tenant, business_date):
        update_factor(
            seeded_db,
            str(tenant.id),
            FactorKind.FORECAST_BIAS,
            "total_units",
            1.5,
            half_life=7,
            clamp_low=0.75,
            clamp_high=1.30,
            business_date=business_date,
        )
        update_factor(
            seeded_db,
            str(tenant.id),
            FactorKind.FORECAST_BIAS,
            "total_units",
            1.1,
            half_life=7,
            clamp_low=0.75,
            clamp_high=1.30,
            business_date=business_date + timedelta(days=1),
        )
        factor = seeded_db.scalar(
            select(CorrectionFactor)
            .where(CorrectionFactor.tenant_id == tenant.id)
            .where(CorrectionFactor.kind == FactorKind.FORECAST_BIAS)
        )
        assert factor.consecutive_clamps == 0


class TestStructuralChangeAlert:
    def test_fires_after_consecutive_clamps(self, seeded_db, tenant, business_date):
        for i in range(6):
            update_factor(
                seeded_db,
                str(tenant.id),
                FactorKind.FORECAST_BIAS,
                "total_units",
                1.5,
                half_life=7,
                clamp_low=0.75,
                clamp_high=1.30,
                business_date=business_date + timedelta(days=i),
                clamp_alert_threshold=5,
            )
        anomaly = seeded_db.scalar(
            select(Anomaly)
            .where(Anomaly.tenant_id == tenant.id)
            .where(Anomaly.anomaly_type == AnomalyType.CONDITION_CHANGE)
        )
        assert anomaly is not None
        assert "pinned" in anomaly.evidence_sentence

    def test_no_alert_below_threshold(self, seeded_db, tenant, business_date):
        for i in range(4):
            update_factor(
                seeded_db,
                str(tenant.id),
                FactorKind.FORECAST_BIAS,
                "total_units",
                1.5,
                half_life=7,
                clamp_low=0.75,
                clamp_high=1.30,
                business_date=business_date + timedelta(days=i),
                clamp_alert_threshold=5,
            )
        anomaly = seeded_db.scalar(
            select(Anomaly)
            .where(Anomaly.tenant_id == tenant.id)
            .where(Anomaly.anomaly_type == AnomalyType.CONDITION_CHANGE)
        )
        assert anomaly is None


class TestGetFactor:
    def test_returns_value(self, seeded_db, tenant, business_date):
        update_factor(
            seeded_db,
            str(tenant.id),
            FactorKind.FORECAST_BIAS,
            "total_units",
            1.15,
            half_life=7,
            clamp_low=0.75,
            clamp_high=1.30,
            business_date=business_date,
        )
        value = get_factor(
            seeded_db, str(tenant.id), FactorKind.FORECAST_BIAS, "total_units"
        )
        assert float(value) == pytest.approx(1.15, abs=0.001)

    def test_returns_default_when_missing(self, seeded_db, tenant):
        value = get_factor(
            seeded_db, str(tenant.id), FactorKind.FORECAST_BIAS, "nonexistent"
        )
        assert value == 1.0

    def test_returns_custom_default(self, seeded_db, tenant):
        value = get_factor(
            seeded_db, str(tenant.id), FactorKind.SHRINKAGE, "nonexistent", default=0.0
        )
        assert value == 0.0


class TestResetFactor:
    def test_resets_to_default(self, seeded_db, tenant, business_date):
        update_factor(
            seeded_db,
            str(tenant.id),
            FactorKind.FORECAST_BIAS,
            "total_units",
            1.2,
            half_life=7,
            clamp_low=0.75,
            clamp_high=1.30,
            business_date=business_date,
        )
        reset_factor(seeded_db, str(tenant.id), FactorKind.FORECAST_BIAS, "total_units", business_date)

        factor = seeded_db.scalar(
            select(CorrectionFactor)
            .where(CorrectionFactor.tenant_id == tenant.id)
            .where(CorrectionFactor.kind == FactorKind.FORECAST_BIAS)
        )
        assert float(factor.value) == 1.0
        assert factor.evidence_count == 0
        assert factor.consecutive_clamps == 0

    def test_reset_logs_history(self, seeded_db, tenant, business_date):
        update_factor(
            seeded_db,
            str(tenant.id),
            FactorKind.FORECAST_BIAS,
            "total_units",
            1.2,
            half_life=7,
            clamp_low=0.75,
            clamp_high=1.30,
            business_date=business_date,
        )
        reset_factor(seeded_db, str(tenant.id), FactorKind.FORECAST_BIAS, "total_units", business_date)

        histories = seeded_db.scalars(
            select(FactorHistory)
            .where(FactorHistory.tenant_id == tenant.id)
            .order_by(FactorHistory.created_at)
        ).all()
        assert len(histories) == 2
        assert float(histories[1].new_value) == 1.0

    def test_reset_nonexistent_does_nothing(self, seeded_db, tenant, business_date):
        reset_factor(seeded_db, str(tenant.id), FactorKind.FORECAST_BIAS, "nonexistent", business_date)
        histories = seeded_db.scalars(
            select(FactorHistory).where(FactorHistory.tenant_id == tenant.id)
        ).all()
        assert len(histories) == 0


class TestConvergence:
    def test_persistent_bias_converges_within_half_life(
        self, seeded_db, tenant, business_date
    ):
        half_life = 7
        update_factor(
            seeded_db,
            str(tenant.id),
            FactorKind.FORECAST_BIAS,
            "total_units",
            1.2,
            half_life=half_life,
            clamp_low=0.75,
            clamp_high=1.30,
            business_date=business_date,
        )

        for i in range(1, 7):
            update_factor(
                seeded_db,
                str(tenant.id),
                FactorKind.FORECAST_BIAS,
                "total_units",
                1.2,
                half_life=half_life,
                clamp_low=0.75,
                clamp_high=1.30,
                business_date=business_date + timedelta(days=i),
            )

        factor = seeded_db.scalar(
            select(CorrectionFactor)
            .where(CorrectionFactor.tenant_id == tenant.id)
            .where(CorrectionFactor.kind == FactorKind.FORECAST_BIAS)
        )
        assert float(factor.value) == pytest.approx(1.2, abs=0.01)
        assert factor.consecutive_clamps == 0
