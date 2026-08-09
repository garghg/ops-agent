from datetime import date, timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.db.models import (
    DailyActual,
    Forecast,
    ForecastMetric,
)
from src.services.forecast_service.config import (
    COVERAGE_LOWER_BOUND,
    COVERAGE_UPPER_BOUND,
    HORIZON_LONG,
    HORIZON_MEDIUM,
    HORIZON_SHORT,
    MIN_BUCKET_SAMPLES,
    MIN_SKILL_IMPROVEMENT,
    PROMOTION_LOOKBACK_DAYS,
    QUANTILE_LABELS,
    QUANTILE_LEVELS,
    RESIDUAL_WINDOW_DAYS,
)


def compute_forecast_metrics(session: Session, tenant_id: str, metric_date: str):
    metric_date = date.fromisoformat(metric_date)
    actuals = session.scalars(
        select(DailyActual)
        .where(DailyActual.tenant_id == tenant_id)
        .where(DailyActual.actual_date == metric_date)
    ).all()

    forecasts = session.scalars(
        select(Forecast)
        .where(Forecast.tenant_id == tenant_id)
        .where(Forecast.target_date == metric_date)
    ).all()

    actuals_by_series = {a.series: a for a in actuals}

    for forecast in forecasts:
        actual = actuals_by_series.get(forecast.series)
        if not actual:
            continue
        mae = abs(forecast.point_estimate - actual.value)
        bias = forecast.point_estimate - actual.value
        coverage = None
        if forecast.quantile_grid:
            low = min(forecast.quantile_grid.values())
            high = max(forecast.quantile_grid.values())
            coverage = False
            if low <= actual.value <= high:
                coverage = True

        stmt = insert(ForecastMetric).values(
            tenant_id=tenant_id,
            series=forecast.series,
            target_date=metric_date,
            model_version=forecast.model_version,
            mae=mae,
            bias=bias,
            coverage=coverage,
            forecast_date=forecast.forecast_date,
        )

        stmt = stmt.on_conflict_do_update(
            constraint="forecast_metrics_tenant_series_target_model_fcdate_key",
            set_={
                "mae": stmt.excluded.mae,
                "bias": stmt.excluded.bias,
                "coverage": stmt.excluded.coverage,
            },
        )

        session.execute(stmt)

    session.commit()


def compute_quantile_grid(
    session: Session, tenant_id: str, model_version: str, as_of_date: str
):
    as_of_date = date.fromisoformat(as_of_date)

    metrics = session.scalars(
        select(ForecastMetric)
        .where(ForecastMetric.tenant_id == tenant_id)
        .where(
            ForecastMetric.target_date
            >= as_of_date - timedelta(days=RESIDUAL_WINDOW_DAYS)
        )
        .where(ForecastMetric.target_date < as_of_date)
        .where(ForecastMetric.model_version == model_version)
    ).all()

    if not metrics:
        return

    short = []
    med = []
    long = []
    for metric in metrics:
        horizon = (metric.target_date - metric.forecast_date).days
        if HORIZON_SHORT[0] <= horizon <= HORIZON_SHORT[1]:
            short.append(float(metric.bias))
        elif HORIZON_MEDIUM[0] <= horizon <= HORIZON_MEDIUM[1]:
            med.append(float(metric.bias))
        elif HORIZON_LONG[0] <= horizon <= HORIZON_LONG[1]:
            long.append(float(metric.bias))

    result = {}

    if len(short) >= MIN_BUCKET_SAMPLES:
        short_quantile = np.percentile(short, QUANTILE_LEVELS)
        result["short"] = dict(zip(QUANTILE_LABELS, [float(v) for v in short_quantile]))
    if len(med) >= MIN_BUCKET_SAMPLES:
        med_quantile = np.percentile(med, QUANTILE_LEVELS)
        result["medium"] = dict(zip(QUANTILE_LABELS, [float(v) for v in med_quantile]))
    if len(long) >= MIN_BUCKET_SAMPLES:
        long_quantile = np.percentile(long, QUANTILE_LEVELS)
        result["long"] = dict(zip(QUANTILE_LABELS, [float(v) for v in long_quantile]))

    return result if result else None


def check_promotion_gate(
    session: Session, tenant_id: str, as_of_date: str, champion: str, challenger: str
):
    as_of_date = date.fromisoformat(as_of_date)

    if champion == challenger:
        return

    champion_metrics = session.scalars(
        select(ForecastMetric)
        .where(ForecastMetric.tenant_id == tenant_id)
        .where(ForecastMetric.model_version == champion)
        .where(
            ForecastMetric.target_date
            >= as_of_date - timedelta(days=PROMOTION_LOOKBACK_DAYS)
        )
        .where(ForecastMetric.target_date < as_of_date)
    ).all()

    challenger_metrics = session.scalars(
        select(ForecastMetric)
        .where(ForecastMetric.tenant_id == tenant_id)
        .where(ForecastMetric.model_version == challenger)
        .where(
            ForecastMetric.target_date
            >= as_of_date - timedelta(days=PROMOTION_LOOKBACK_DAYS)
        )
        .where(ForecastMetric.target_date < as_of_date)
    ).all()

    actuals = session.scalars(
        select(DailyActual)
        .where(DailyActual.tenant_id == tenant_id)
        .where(
            DailyActual.actual_date
            >= as_of_date - timedelta(days=PROMOTION_LOOKBACK_DAYS)
        )
        .where(DailyActual.actual_date < as_of_date)
    ).all()

    if not (champion_metrics and challenger_metrics and actuals):
        return

    total_actual = sum(actual.value for actual in actuals)
    challenger_total_mae = sum(clm.mae for clm in challenger_metrics)
    champion_total_mae = sum(chm.mae for chm in champion_metrics)
    challenger_coverage_total = sum(
        1 for clm in challenger_metrics if clm.coverage is not None
    )
    challenger_coverage_true = sum(1 for clm in challenger_metrics if clm.coverage)

    challenger_wape = None
    champion_wape = None
    if total_actual != 0:
        challenger_wape = challenger_total_mae / total_actual
        champion_wape = champion_total_mae / total_actual
    
    skills = None
    if challenger_wape is not None and champion_wape is not None:
        skills = 1 - (challenger_wape / champion_wape)
    
    coverage_pct = None
    if challenger_coverage_total != 0:
        coverage_pct = (challenger_coverage_true / challenger_coverage_total) * 100
    
    passed = False
    if (
        skills is not None
        and skills > MIN_SKILL_IMPROVEMENT
        and coverage_pct is not None
        and COVERAGE_LOWER_BOUND <= coverage_pct <= COVERAGE_UPPER_BOUND
    ):
        passed = True

    return {
        "skills": skills,
        "coverage_pct": coverage_pct,
        "passed": passed,
    }
