from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from src.db.models import (
    Anomaly,
    DailyActual,
    Forecast,
    IntradayProfile,
    SaleLineItem,
    SaleTransaction,
    Tenant,
)
from src.schemas.anomaly import AnomalySubject, AnomalyType
from src.services.anomaly_service import (
    _persist_anomaly,
    run_day_close_checks,
    run_intraday_check,
)


@pytest.fixture
def tenant(seeded_db):
    return seeded_db.scalar(select(Tenant).limit(1))


@pytest.fixture
def business_date():
    return date(2026, 8, 5)


@pytest.fixture
def forecast_with_grid(seeded_db, tenant, business_date):
    for series in ["total_units", "total_revenue"]:
        seeded_db.add(
            Forecast(
                tenant_id=tenant.id,
                series=series,
                target_date=business_date,
                forecast_date=business_date - timedelta(days=1),
                model_version="poisson_glm",
                point_estimate=Decimal("200.00")
                if series == "total_units"
                else Decimal("900.00"),
                quantile_grid={
                    "p05": 140.0 if series == "total_units" else 600.0,
                    "p20": 160.0 if series == "total_units" else 700.0,
                    "p50": 200.0 if series == "total_units" else 900.0,
                    "p80": 240.0 if series == "total_units" else 1100.0,
                    "p90": 260.0 if series == "total_units" else 1200.0,
                    "p95": 280.0 if series == "total_units" else 1300.0,
                },
            )
        )
    seeded_db.commit()


@pytest.fixture
def actuals_normal(seeded_db, tenant, business_date):
    seeded_db.add(
        DailyActual(
            tenant_id=tenant.id,
            series="total_units",
            actual_date=business_date,
            value=Decimal("200.00"),
        )
    )
    seeded_db.add(
        DailyActual(
            tenant_id=tenant.id,
            series="total_revenue",
            actual_date=business_date,
            value=Decimal("900.00"),
        )
    )
    seeded_db.commit()


@pytest.fixture
def actuals_low(seeded_db, tenant, business_date):
    seeded_db.add(
        DailyActual(
            tenant_id=tenant.id,
            series="total_units",
            actual_date=business_date,
            value=Decimal("100.00"),
        )
    )
    seeded_db.add(
        DailyActual(
            tenant_id=tenant.id,
            series="total_revenue",
            actual_date=business_date,
            value=Decimal("400.00"),
        )
    )
    seeded_db.commit()


@pytest.fixture
def intraday_profiles(seeded_db, tenant, business_date):
    dow = business_date.weekday()
    profile_date = business_date - timedelta(days=1)
    hours = {
        10: 0.05,
        11: 0.08,
        12: 0.12,
        13: 0.15,
        14: 0.15,
        15: 0.13,
        16: 0.12,
        17: 0.10,
        18: 0.10,
    }
    for hour, fraction in hours.items():
        seeded_db.add(
            IntradayProfile(
                tenant_id=tenant.id,
                day_of_week=dow,
                hour=hour,
                fraction=Decimal(str(fraction)),
                as_of_date=profile_date,
            )
        )
    seeded_db.commit()


def _add_sales(session, tenant, business_date, count, hour=12):
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(tenant.timezone)
    ts = datetime.combine(business_date, datetime.min.time(), tzinfo=tz).replace(
        hour=hour
    )
    txn = SaleTransaction(
        tenant_id=tenant.id,
        external_transaction_id=f"test-{business_date}-{hour}-{count}",
        source="test",
        timestamp=ts,
        total=Decimal(str(count * 5)),
        payment_method="card",
        transaction_type="sale",
    )
    session.add(txn)
    session.flush()
    session.add(
        SaleLineItem(
            tenant_id=tenant.id,
            sale_transaction_id=txn.id,
            item_name="Test Item",
            quantity=count,
            unit_price=Decimal("5.00"),
        )
    )
    session.commit()


class TestPersistAnomaly:
    def test_basic_insert(self, seeded_db, tenant, business_date):
        _persist_anomaly(
            seeded_db,
            str(tenant.id),
            AnomalyType.VOID_RATE,
            AnomalySubject.VOID_RATE,
            2,
            business_date,
            {"rate": 0.08, "threshold": 0.05},
            "Void rate 8% exceeds threshold 5%",
            48,
        )
        anomaly = seeded_db.scalar(
            select(Anomaly).where(Anomaly.tenant_id == tenant.id)
        )
        assert anomaly is not None
        assert anomaly.anomaly_type == AnomalyType.VOID_RATE
        assert anomaly.suppressed is False

    def test_dedup_updates_evidence(self, seeded_db, tenant, business_date):
        _persist_anomaly(
            seeded_db,
            str(tenant.id),
            AnomalyType.VOID_RATE,
            AnomalySubject.VOID_RATE,
            2,
            business_date,
            {"rate": 0.08},
            "first",
            48,
        )
        _persist_anomaly(
            seeded_db,
            str(tenant.id),
            AnomalyType.VOID_RATE,
            AnomalySubject.VOID_RATE,
            2,
            business_date,
            {"rate": 0.10},
            "second",
            48,
        )
        anomalies = seeded_db.scalars(
            select(Anomaly).where(Anomaly.tenant_id == tenant.id)
        ).all()
        assert len(anomalies) == 1
        assert anomalies[0].evidence_sentence == "second"
        assert anomalies[0].evidence["rate"] == 0.10

    def test_cooldown_suppresses(self, seeded_db, tenant, business_date):
        yesterday = business_date - timedelta(days=1)
        _persist_anomaly(
            seeded_db,
            str(tenant.id),
            AnomalyType.VOID_RATE,
            AnomalySubject.VOID_RATE,
            2,
            yesterday,
            {"rate": 0.08},
            "yesterday's alert",
            48,
        )
        _persist_anomaly(
            seeded_db,
            str(tenant.id),
            AnomalyType.VOID_RATE,
            AnomalySubject.VOID_RATE,
            2,
            business_date,
            {"rate": 0.09},
            "today's alert",
            48,
        )
        today_anomaly = seeded_db.scalar(
            select(Anomaly)
            .where(Anomaly.tenant_id == tenant.id)
            .where(Anomaly.business_date == business_date)
        )
        assert today_anomaly.suppressed is True

    def test_no_cooldown_outside_window(self, seeded_db, tenant, business_date):
        old_date = business_date - timedelta(days=5)
        _persist_anomaly(
            seeded_db,
            str(tenant.id),
            AnomalyType.VOID_RATE,
            AnomalySubject.VOID_RATE,
            2,
            old_date,
            {"rate": 0.08},
            "old alert",
            48,
        )
        _persist_anomaly(
            seeded_db,
            str(tenant.id),
            AnomalyType.VOID_RATE,
            AnomalySubject.VOID_RATE,
            2,
            business_date,
            {"rate": 0.09},
            "new alert",
            48,
        )
        today_anomaly = seeded_db.scalar(
            select(Anomaly)
            .where(Anomaly.tenant_id == tenant.id)
            .where(Anomaly.business_date == business_date)
        )
        assert today_anomaly.suppressed is False


class TestDayCloseChecks:
    def test_residual_fires_when_outside_band(
        self, seeded_db, tenant, business_date, forecast_with_grid, actuals_low
    ):
        run_day_close_checks(seeded_db, str(tenant.id), business_date)
        anomalies = seeded_db.scalars(
            select(Anomaly)
            .where(Anomaly.tenant_id == tenant.id)
            .where(Anomaly.anomaly_type == AnomalyType.FORECAST_RESIDUAL)
        ).all()
        assert len(anomalies) == 2
        subjects = {a.subject for a in anomalies}
        assert AnomalySubject.TOTAL_UNITS in subjects
        assert AnomalySubject.TOTAL_REVENUE in subjects

    def test_no_anomaly_when_inside_band(
        self, seeded_db, tenant, business_date, forecast_with_grid, actuals_normal
    ):
        run_day_close_checks(seeded_db, str(tenant.id), business_date)
        anomalies = seeded_db.scalars(
            select(Anomaly)
            .where(Anomaly.tenant_id == tenant.id)
            .where(Anomaly.anomaly_type == AnomalyType.FORECAST_RESIDUAL)
        ).all()
        assert len(anomalies) == 0

    def test_hard_rule_fires(self, seeded_db, tenant, business_date):
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(tenant.timezone)
        day_start = datetime.combine(business_date, datetime.min.time(), tzinfo=tz)
        for i in range(100):
            txn_type = "void" if i < 10 else "sale"
            txn = SaleTransaction(
                tenant_id=tenant.id,
                external_transaction_id=f"hard-rule-{i}",
                source="test",
                timestamp=day_start + timedelta(minutes=i),
                total=Decimal("5.00"),
                payment_method="card",
                transaction_type=txn_type,
            )
            seeded_db.add(txn)
        seeded_db.commit()

        run_day_close_checks(seeded_db, str(tenant.id), business_date)
        anomaly = seeded_db.scalar(
            select(Anomaly)
            .where(Anomaly.tenant_id == tenant.id)
            .where(Anomaly.anomaly_type == AnomalyType.VOID_RATE)
        )
        assert anomaly is not None

    def test_no_crash_without_data(self, seeded_db, tenant, business_date):
        run_day_close_checks(seeded_db, str(tenant.id), business_date)
        anomalies = seeded_db.scalars(
            select(Anomaly).where(Anomaly.tenant_id == tenant.id)
        ).all()
        assert len(anomalies) == 0


class TestIntradayCheck:
    def test_below_pace_fires(
        self, seeded_db, tenant, business_date, forecast_with_grid, intraday_profiles
    ):
        _add_sales(seeded_db, tenant, business_date, 20, hour=11)
        run_intraday_check(seeded_db, str(tenant.id), business_date)
        anomaly = seeded_db.scalar(
            select(Anomaly)
            .where(Anomaly.tenant_id == tenant.id)
            .where(Anomaly.anomaly_type == AnomalyType.INTRADAY_PACE)
        )
        assert anomaly is not None
        assert anomaly.evidence["checkpoint_hour"] == 14

    def test_on_pace_no_anomaly(
        self, seeded_db, tenant, business_date, forecast_with_grid, intraday_profiles
    ):
        for h in [10, 11, 12, 13]:
            _add_sales(seeded_db, tenant, business_date, 25, hour=h)
        run_intraday_check(seeded_db, str(tenant.id), business_date)
        anomaly = seeded_db.scalar(
            select(Anomaly)
            .where(Anomaly.tenant_id == tenant.id)
            .where(Anomaly.anomaly_type == AnomalyType.INTRADAY_PACE)
        )
        assert anomaly is None

    def test_no_crash_without_forecast(self, seeded_db, tenant, business_date):
        run_intraday_check(seeded_db, str(tenant.id), business_date)
        anomalies = seeded_db.scalars(
            select(Anomaly).where(Anomaly.tenant_id == tenant.id)
        ).all()
        assert len(anomalies) == 0

    def test_no_crash_without_profiles(
        self, seeded_db, tenant, business_date, forecast_with_grid
    ):
        run_intraday_check(seeded_db, str(tenant.id), business_date)
        anomalies = seeded_db.scalars(
            select(Anomaly).where(Anomaly.tenant_id == tenant.id)
        ).all()
        assert len(anomalies) == 0
