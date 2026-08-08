from datetime import date, datetime, timedelta
from datetime import time as dt_time
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.clock import get_now
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
from src.schemas.forecast import ForecastSeries
from src.schemas.sale import SaleTransactionType
from src.services.alert_service import check_financial_alerts
from src.services.config_services import resolve_config
from src.services.utils import get_sales_summary


def _persist_anomaly(
    session: Session,
    tenant_id: str,
    anomaly_type: str,
    subject: str,
    severity: int,
    business_date: date,
    evidence: dict,
    evidence_sentence: str,
    cooldown_hours: int,
):
    dedup_key = f"{anomaly_type}:{subject}:{business_date!s}"
    utc_time = get_now()
    timezone = session.scalar(select(Tenant.timezone).where(Tenant.id == tenant_id))
    local_time = utc_time.astimezone(ZoneInfo(timezone))
    cooldown_start = local_time - timedelta(hours=cooldown_hours)
    suppressed = False

    recent = session.scalar(
        select(Anomaly)
        .where(Anomaly.tenant_id == tenant_id)
        .where(Anomaly.created_at > cooldown_start)
        .where(Anomaly.suppressed.is_(False))
        .where(Anomaly.anomaly_type == anomaly_type)
        .where(Anomaly.subject == subject)
        .limit(1)
    )

    if recent:
        suppressed = True

    stmt = insert(Anomaly).values(
        tenant_id=tenant_id,
        anomaly_type=anomaly_type,
        subject=subject,
        severity=severity,
        business_date=business_date,
        evidence=evidence,
        evidence_sentence=evidence_sentence,
        dedup_key=dedup_key,
        suppressed=suppressed,
    )

    stmt = stmt.on_conflict_do_update(
        constraint="anomalies_tenant_id_dedup_key_key",
        set_={
            "evidence": stmt.excluded.evidence,
            "evidence_sentence": stmt.excluded.evidence_sentence,
        },
    )
    session.execute(stmt)
    session.commit()


def run_day_close_checks(session: Session, tenant_id: str, business_date: date):
    config = resolve_config(tenant_id, session)

    actual_revenue = session.scalar(
        select(DailyActual.value)
        .where(DailyActual.tenant_id == tenant_id)
        .where(DailyActual.series == ForecastSeries.TOTAL_REVENUE)
        .where(DailyActual.actual_date == business_date)
    )

    actual_units = session.scalar(
        select(DailyActual.value)
        .where(DailyActual.tenant_id == tenant_id)
        .where(DailyActual.series == ForecastSeries.TOTAL_UNITS)
        .where(DailyActual.actual_date == business_date)
    )

    forecast_revenue_qg = session.scalar(
        select(Forecast.quantile_grid)
        .where(Forecast.tenant_id == tenant_id)
        .where(Forecast.series == ForecastSeries.TOTAL_REVENUE)
        .where(Forecast.target_date == business_date)
        .order_by(Forecast.forecast_date.desc())
        .limit(1)
    )

    forecast_units_qg = session.scalar(
        select(Forecast.quantile_grid)
        .where(Forecast.tenant_id == tenant_id)
        .where(Forecast.series == ForecastSeries.TOTAL_UNITS)
        .where(Forecast.target_date == business_date)
        .order_by(Forecast.forecast_date.desc())
        .limit(1)
    )

    severity = 1 if AnomalyType.FORECAST_RESIDUAL in config.anomalies.tier1_types else 2

    if actual_revenue is not None and forecast_revenue_qg:
        low_rev = forecast_revenue_qg["p05"]
        high_rev = forecast_revenue_qg["p90"]

        if not (low_rev <= actual_revenue <= high_rev):
            _persist_anomaly(
                session,
                tenant_id,
                AnomalyType.FORECAST_RESIDUAL,
                AnomalySubject.TOTAL_REVENUE,
                severity,
                business_date,
                {
                    "predicted_min": low_rev,
                    "predicted_max": high_rev,
                    "actual_revenue": actual_revenue,
                },
                f"Revenue expected between ${low_rev} and ${high_rev}; Actual revenue: ${actual_revenue}",
                config.anomalies.cooldown_hours,
            )

    if actual_units is not None and forecast_units_qg:
        low_units = forecast_units_qg["p05"]
        high_units = forecast_units_qg["p90"]

        if not (low_units <= actual_units <= high_units):
            _persist_anomaly(
                session,
                tenant_id,
                AnomalyType.FORECAST_RESIDUAL,
                AnomalySubject.TOTAL_UNITS,
                severity,
                business_date,
                {
                    "predicted_min": low_units,
                    "predicted_max": high_units,
                    "actual_units_sold": actual_units,
                },
                f"Unit sales expected between {low_units} and {high_units}; Actual sales: {actual_units}",
                config.anomalies.cooldown_hours,
            )

    tz = ZoneInfo(session.scalar(select(Tenant.timezone).where(Tenant.id == tenant_id)))
    day_start = datetime.combine(business_date, dt_time.min, tzinfo=tz)
    day_end = datetime.combine(
        business_date + timedelta(days=1), dt_time.min, tzinfo=tz
    )
    sales_summary = get_sales_summary(session, tenant_id, day_start, day_end)
    alerts = check_financial_alerts(sales_summary, config.alerts)

    if alerts:
        for alert in alerts:
            severity = 1 if alert["type"] in config.anomalies.tier1_types else 2
            _persist_anomaly(
                session,
                tenant_id,
                alert["type"],
                alert["subject"],
                severity,
                business_date,
                alert,
                f"Expected {alert['subject']}: {alert['expected']}; Actual {alert['subject']}: {alert['rate']}",
                config.anomalies.cooldown_hours,
            )


def run_intraday_check(session: Session, tenant_id: str, business_date: date):
    config = resolve_config(tenant_id, session)

    if not config:
        return

    forecast_units = session.execute(
        select(Forecast.quantile_grid, Forecast.point_estimate)
        .where(Forecast.tenant_id == tenant_id)
        .where(Forecast.series == ForecastSeries.TOTAL_UNITS)
        .where(Forecast.target_date == business_date)
        .order_by(Forecast.forecast_date.desc())
    ).first()

    if not forecast_units:
        return

    quantile_grid, point_estimate = forecast_units
    checkpoint_hour = config.anomalies.checkpoint_hour

    latest_profile_date = session.scalar(
        select(IntradayProfile.as_of_date)
        .where(IntradayProfile.tenant_id == tenant_id)
        .where(IntradayProfile.day_of_week == business_date.weekday())
        .where(IntradayProfile.as_of_date <= business_date)
        .order_by(IntradayProfile.as_of_date.desc())
        .limit(1)
    )

    if not latest_profile_date:
        return

    profiles = session.execute(
        select(IntradayProfile.hour, IntradayProfile.fraction)
        .where(IntradayProfile.tenant_id == tenant_id)
        .where(IntradayProfile.day_of_week == business_date.weekday())
        .where(IntradayProfile.as_of_date == latest_profile_date)
    ).all()

    expected_cp_sales_factor = sum(
        fraction for hour, fraction in profiles if hour <= checkpoint_hour
    )

    expected_cp_sales = point_estimate * expected_cp_sales_factor

    timezone = session.scalar(select(Tenant.timezone).where(Tenant.id == tenant_id))
    tz = ZoneInfo(timezone)
    day_start = datetime.combine(business_date, dt_time.min, tzinfo=tz)
    checkpoint_time = datetime.combine(
        business_date, dt_time(checkpoint_hour), tzinfo=tz
    )

    total_sales = session.scalar(
        select(func.coalesce(func.sum(SaleLineItem.quantity), 0))
        .join(SaleTransaction, SaleTransaction.id == SaleLineItem.sale_transaction_id)
        .where(SaleTransaction.tenant_id == tenant_id)
        .where(SaleTransaction.timestamp >= day_start)
        .where(SaleTransaction.timestamp < checkpoint_time)
        .where(SaleTransaction.transaction_type == SaleTransactionType.SALE.value)
    )

    if quantile_grid:
        low = quantile_grid["p05"] * expected_cp_sales_factor
        high = quantile_grid["p90"] * expected_cp_sales_factor

        if not (low <= total_sales <= high):
            severity = (
                1 if AnomalyType.INTRADAY_PACE in config.anomalies.tier1_types else 2
            )
            _persist_anomaly(
                session,
                tenant_id,
                AnomalyType.INTRADAY_PACE,
                AnomalySubject.TOTAL_UNITS,
                severity,
                business_date,
                {
                    "checkpoint_hour": checkpoint_hour,
                    "actual_sales": float(total_sales),
                    "expected_sales": float(expected_cp_sales),
                    "expected_low": float(low),
                    "expected_high": float(high),
                    "pct_of_day": float(expected_cp_sales_factor),
                },
                f"Sales through {checkpoint_hour}:00 are {total_sales} vs {low:.0f}-{high:.0f} expected ({expected_cp_sales_factor:.0%} of day complete).",
                config.anomalies.cooldown_hours,
            )
