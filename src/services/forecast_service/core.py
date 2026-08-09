from collections import defaultdict
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from decimal import Decimal
from zoneinfo import ZoneInfo

import pandas as pd
from lightgbm import LGBMRegressor
from sklearn.base import BaseEstimator
from sklearn.linear_model import PoissonRegressor
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from src.db.models import (
    DailyActual,
    Forecast,
    ModelRegistry,
    SaleLineItem,
    SaleTransaction,
    Tenant,
    WeatherObservation,
)
from src.schemas.forecast import ForecastSeries
from src.schemas.learning import FactorKind
from src.schemas.models import ModelVersion
from src.schemas.sale import SaleTransactionType
from src.schemas.weather import WeatherSource
from src.services.config_services import resolve_config
from src.services.forecast_service.config import (
    FORECAST_HORIZON,
    HORIZON_LONG,
    HORIZON_MEDIUM,
    HORIZON_SHORT,
    SEASONAL_NAIVE_LOOKBACK_DAYS,
    TRAILING_MEAN_LOOKBACK_DAYS,
)
from src.services.forecast_service.metrics import (
    compute_forecast_metrics,
    compute_quantile_grid,
)
from src.services.learning_service import get_factor, update_factor


def actuals_aggregate(session: Session, tenant_id: str, business_date: str):
    tenant = session.scalar(select(Tenant).where(Tenant.id == tenant_id))
    business_date = date.fromisoformat(business_date)
    tz = ZoneInfo(tenant.timezone)
    day_start = datetime.combine(business_date, dt_time.min, tzinfo=tz)
    day_end = datetime.combine(
        business_date + timedelta(days=1), dt_time.min, tzinfo=tz
    )

    revenue = session.scalar(
        select(func.coalesce(func.sum(SaleTransaction.total), Decimal(0))).where(
            SaleTransaction.tenant_id == tenant_id,
            SaleTransaction.transaction_type == SaleTransactionType.SALE,
            SaleTransaction.timestamp >= day_start,
            SaleTransaction.timestamp < day_end,
        )
    )

    total_units = session.scalar(
        select(func.coalesce(func.sum(SaleLineItem.quantity), 0))
        .join(SaleTransaction, SaleLineItem.sale_transaction_id == SaleTransaction.id)
        .where(
            SaleTransaction.tenant_id == tenant_id,
            SaleTransaction.transaction_type == SaleTransactionType.SALE,
            SaleTransaction.timestamp >= day_start,
            SaleTransaction.timestamp < day_end,
        )
    )

    revenue_stmt = insert(DailyActual).values(
        tenant_id=tenant_id,
        series=ForecastSeries.TOTAL_REVENUE,
        actual_date=business_date,
        value=revenue,
    )
    revenue_stmt = revenue_stmt.on_conflict_do_update(
        constraint="daily_actuals_tenant_series_date_key",
        set_={"value": revenue_stmt.excluded.value},
    )

    session.execute(revenue_stmt)

    units_stmt = insert(DailyActual).values(
        tenant_id=tenant_id,
        series=ForecastSeries.TOTAL_UNITS,
        actual_date=business_date,
        value=total_units,
    )
    units_stmt = units_stmt.on_conflict_do_update(
        constraint="daily_actuals_tenant_series_date_key",
        set_={"value": units_stmt.excluded.value},
    )

    session.execute(units_stmt)
    session.commit()


def forecast_seasonal_naive(
    session: Session, tenant_id: str, as_of_date: str, flush_only: bool = False
):
    as_of_date = date.fromisoformat(as_of_date)
    lookback = as_of_date - timedelta(days=SEASONAL_NAIVE_LOOKBACK_DAYS)
    actuals = session.execute(
        select(DailyActual.series, DailyActual.actual_date, DailyActual.value).where(
            DailyActual.tenant_id == tenant_id,
            DailyActual.actual_date >= lookback,
            DailyActual.actual_date < as_of_date,
        )
    ).all()

    by_weekday = defaultdict(list)
    for row in actuals:
        key = (row.series, row.actual_date.weekday())
        by_weekday[key].append(row.value)

    series_names = {row.series for row in actuals}

    for offset in range(1, FORECAST_HORIZON + 1):
        target = as_of_date + timedelta(days=offset)
        weekday = target.weekday()
        for series in series_names:
            numbers = by_weekday[(series, weekday)]
            if not numbers:
                continue
            average = sum(numbers) / len(numbers)

            stmt = insert(Forecast).values(
                tenant_id=tenant_id,
                series=series,
                target_date=target,
                model_version=ModelVersion.SEASONAL_NAIVE.value,
                point_estimate=average,
                forecast_date=as_of_date,
            )

            stmt = stmt.on_conflict_do_update(
                constraint="forecasts_tenant_series_target_model_fcdate_key",
                set_={"point_estimate": stmt.excluded.point_estimate},
            )

            session.execute(stmt)

    if flush_only:
        session.flush()
    else:
        session.commit()


def forecast_trailing_mean(
    session: Session, tenant_id: str, as_of_date: str, flush_only: bool = False
):
    as_of_date = date.fromisoformat(as_of_date)
    lookback = as_of_date - timedelta(days=TRAILING_MEAN_LOOKBACK_DAYS)
    actuals = session.execute(
        select(DailyActual.series, DailyActual.actual_date, DailyActual.value).where(
            DailyActual.tenant_id == tenant_id,
            DailyActual.actual_date >= lookback,
            DailyActual.actual_date < as_of_date,
        )
    ).all()

    series_names = {row.series for row in actuals}

    for series in series_names:
        numbers = [row.value for row in actuals if row.series == series]
        if not numbers:
            continue
        average = sum(numbers) / len(numbers)
        for offset in range(1, FORECAST_HORIZON + 1):
            target = as_of_date + timedelta(days=offset)
            stmt = insert(Forecast).values(
                tenant_id=tenant_id,
                series=series,
                target_date=target,
                model_version=ModelVersion.TRAILING_7D_MEAN.value,
                point_estimate=average,
                forecast_date=as_of_date,
            )

            stmt = stmt.on_conflict_do_update(
                constraint="forecasts_tenant_series_target_model_fcdate_key",
                set_={"point_estimate": stmt.excluded.point_estimate},
            )

            session.execute(stmt)

    if flush_only:
        session.flush()
    else:
        session.commit()


def build_features(
    session: Session, tenant_id: str, target_date: str, weather_source: str
):
    target_date = date.fromisoformat(target_date)

    weather = session.scalar(
        select(WeatherObservation)
        .where(WeatherObservation.tenant_id == tenant_id)
        .where(WeatherObservation.observation_date == target_date)
        .where(WeatherObservation.source == weather_source)
    )

    if not weather:
        return

    return {
        "day_of_week": target_date.weekday(),
        "month": target_date.month,
        "is_holiday": False,
        "is_school_break": False,
        "max_temp": float(weather.max_temp_c),
        "precipitation": float(weather.precipitation_mm),
    }


def _train_model(
    session: Session,
    tenant_id: str,
    series: str,
    model: BaseEstimator,
    cutoff_date: date | None = None,
) -> tuple | None:
    query = (
        select(DailyActual)
        .where(DailyActual.tenant_id == tenant_id)
        .where(DailyActual.series == series)
    )

    if cutoff_date is not None:
        query = query.where(DailyActual.actual_date < cutoff_date)

    actuals = session.scalars(query).all()

    if not actuals:
        return

    features = []
    targets = []
    for actual in actuals:
        f = build_features(
            session,
            str(actual.tenant_id),
            str(actual.actual_date),
            WeatherSource.ACTUAL.value,
        )
        if f is None:
            continue
        features.append(f)
        targets.append(float(actual.value))

    if not features:
        return
    df = pd.DataFrame(features)
    df = pd.get_dummies(df, columns=["day_of_week", "month"])
    model.fit(df, targets)
    return model, df.columns.tolist()


def train_glm(
    session: Session, tenant_id: str, series: str, cutoff_date: date | None = None
):
    return _train_model(session, tenant_id, series, PoissonRegressor(), cutoff_date)


def train_lgbm(
    session: Session, tenant_id: str, series: str, cutoff_date: date | None = None
):
    return _train_model(
        session,
        tenant_id,
        series,
        LGBMRegressor(n_estimators=50, num_leaves=8, min_child_samples=5, verbose=-1),
        cutoff_date,
    )


TRAIN_DISPATCH = {
    ModelVersion.POISSON_GLM.value: train_glm,
    ModelVersion.LIGHTGBM.value: train_lgbm,
}


def _forecast_data(
    session: Session,
    tenant_id: str,
    as_of_date: str,
    model_name: str,
    flush_only: bool = False,
):
    series_lst = session.scalars(
        select(DailyActual.series).where(DailyActual.tenant_id == tenant_id).distinct()
    ).all()

    if not series_lst:
        return

    as_of_date = date.fromisoformat(as_of_date)
    
    active_model = session.scalar(
        select(ModelRegistry.active_version)
        .where(ModelRegistry.tenant_id == tenant_id)
    )

    if not active_model:
        active_model = ModelVersion.POISSON_GLM.value

    for series in series_lst:
        result = TRAIN_DISPATCH[model_name](session, tenant_id, series, as_of_date)

        if result is None:
            continue

        model, columns = result
        quantile_grid = compute_quantile_grid(
            session, tenant_id, model_name, as_of_date
        )

        for offset in range(1, FORECAST_HORIZON + 1):
            target_date = as_of_date + timedelta(days=offset)
            features = build_features(
                session, tenant_id, str(target_date), WeatherSource.FORECAST.value
            )

            if features is None:
                continue

            df = pd.DataFrame([features])
            df = pd.get_dummies(df, columns=["day_of_week", "month"])
            df = df.reindex(columns=columns, fill_value=0)
            prediction = model.predict(df)[0]
            if model_name == active_model:
                bias = get_factor(
                    session,
                    tenant_id,
                    FactorKind.FORECAST_BIAS,
                    f"{series}:{active_model}",
                )
                prediction = prediction * float(bias)

            bucket_dict = None
            if quantile_grid:
                if HORIZON_SHORT[0] <= offset <= HORIZON_SHORT[1]:
                    bucket_dict = quantile_grid.get("short")
                elif HORIZON_MEDIUM[0] <= offset <= HORIZON_MEDIUM[1]:
                    bucket_dict = quantile_grid.get("medium")
                elif HORIZON_LONG[0] <= offset <= HORIZON_LONG[1]:
                    bucket_dict = quantile_grid.get("long")

            if bucket_dict:
                bucket_dict = {k: float(prediction - v) for k, v in bucket_dict.items()}

            stmt = insert(Forecast).values(
                tenant_id=tenant_id,
                series=series,
                target_date=target_date,
                model_version=model_name,
                point_estimate=prediction,
                forecast_date=as_of_date,
                quantile_grid=bucket_dict,
            )

            stmt = stmt.on_conflict_do_update(
                constraint="forecasts_tenant_series_target_model_fcdate_key",
                set_={
                    "point_estimate": stmt.excluded.point_estimate,
                    "quantile_grid": stmt.excluded.quantile_grid,
                },
            )

            session.execute(stmt)

        if flush_only:
            session.flush()
        else:
            session.commit()


def forecast_glm(
    session: Session, tenant_id: str, as_of_date: str, flush_only: bool = False
):
    _forecast_data(
        session, tenant_id, as_of_date, ModelVersion.POISSON_GLM.value, flush_only
    )


def forecast_lgbm(
    session: Session, tenant_id: str, as_of_date: str, flush_only: bool = False
):
    _forecast_data(
        session, tenant_id, as_of_date, ModelVersion.LIGHTGBM.value, flush_only
    )


FORECAST_DISPATCH = {
    ModelVersion.POISSON_GLM.value: forecast_glm,
    ModelVersion.LIGHTGBM.value: forecast_lgbm,
}


def backtest(
    session: Session,
    tenant_id: str,
    start_date: str,
    end_date: str,
    models: list[str],
    flush_only: bool = False,
):
    start_date = date.fromisoformat(start_date)
    end_date = date.fromisoformat(end_date)

    for offset in range((end_date - start_date).days + 1):
        current_date = start_date + timedelta(days=offset)
        forecast_seasonal_naive(session, tenant_id, str(current_date), flush_only)
        forecast_trailing_mean(session, tenant_id, str(current_date), flush_only)
        for model in models:
            FORECAST_DISPATCH[model](session, tenant_id, str(current_date), flush_only)
        compute_forecast_metrics(session, tenant_id, str(current_date), flush_only)


def update_forecast_bias(session: Session, tenant_id: str, business_date: str):
    business_date = date.fromisoformat(business_date)
    actuals = session.scalars(
        select(DailyActual)
        .where(DailyActual.actual_date == business_date)
        .where(DailyActual.tenant_id == tenant_id)
    ).all()

    if not actuals:
        return

    series_lst = [actual.series for actual in actuals]
    
    active_model = session.scalar(
        select(ModelRegistry.active_version)
        .where(ModelRegistry.tenant_id == tenant_id)
    )

    if not active_model:
        active_model = ModelVersion.POISSON_GLM.value

    forecasts = session.scalars(
        select(Forecast)
        .where(Forecast.target_date == business_date)
        .where(Forecast.tenant_id == tenant_id)
        .where(Forecast.model_version == active_model)
        .order_by(Forecast.forecast_date.desc())
    ).all()

    if not forecasts:
        return

    actual_map = {a.series: float(a.value) for a in actuals}
    forecast_map = {}
    for f in forecasts:
        if f.series not in forecast_map:
            forecast_map[f.series] = float(f.point_estimate)

    config = resolve_config(tenant_id, session)

    for series in series_lst:
        predicted = forecast_map.get(series)
        actual = actual_map.get(series)

        if predicted is None or actual is None or predicted <= 0 or actual <= 0:
            continue

        bias = get_factor(
            session, tenant_id, FactorKind.FORECAST_BIAS, f"{series}:{active_model}"
        )
        raw_prediction = predicted / float(bias)

        if raw_prediction <= 0:
            continue

        observation = actual / raw_prediction

        update_factor(
            session,
            tenant_id,
            FactorKind.FORECAST_BIAS,
            f"{series}:{active_model}",
            observation,
            config.learning.forecast_bias_half_life,
            config.learning.forecast_bias_clamp_low,
            config.learning.forecast_bias_clamp_high,
            business_date,
        )
