from datetime import date, datetime, timedelta
from datetime import time as dt_time
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.clock import get_now
from src.db.models import Anomaly, DailyActual, Forecast, Tenant
from src.schemas.anomaly import AnomalySubject, AnomalyType
from src.schemas.forecast import ForecastSeries
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


def run_day_close_checks(session: Session, tenant_id: str, business_date: str):
    business_date = date.fromisoformat(business_date)
    config = resolve_config(str(tenant_id), session)

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

