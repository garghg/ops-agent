from collections import defaultdict
from datetime import date, datetime, timedelta
from datetime import time as dt_time
from decimal import Decimal
from zoneinfo import ZoneInfo

import pandas as pd
from sklearn.linear_model import PoissonRegressor
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from services.forecast_service.metrics import (
    compute_forecast_metrics,
    compute_quantile_grid,
)
from src.db.models import (
    DailyActual,
    Forecast,
    SaleLineItem,
    SaleTransaction,
    Tenant,
    WeatherObservation,
)
from src.schemas.models import ModelVersion
from src.schemas.sale import SaleTransactionType
from src.schemas.weather import WeatherSource
from src.services.forecast_service.config import (
    FORECAST_HORIZON,
    HORIZON_LONG,
    HORIZON_MEDIUM,
    HORIZON_SHORT,
    SEASONAL_NAIVE_LOOKBACK_DAYS,
    TRAILING_MEAN_LOOKBACK_DAYS,
)


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
        series="total_revenue",
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
        series="total_units",
        actual_date=business_date,
        value=total_units,
    )
    units_stmt = units_stmt.on_conflict_do_update(
        constraint="daily_actuals_tenant_series_date_key",
        set_={"value": units_stmt.excluded.value},
    )

    session.execute(units_stmt)
    session.commit()


def forecast_seasonal_naive(session: Session, tenant_id: str, as_of_date: str):
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

    session.commit()


def forecast_trailing_mean(session: Session, tenant_id: str, as_of_date: str):
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


def train_glm(session: Session, tenant_id: str, series: str):
    actuals = session.scalars(
        select(DailyActual)
        .where(DailyActual.tenant_id == tenant_id)
        .where(DailyActual.series == series)
    ).all()

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
    model = PoissonRegressor()
    model.fit(df, targets)
    return model, df.columns.tolist()


def forecast_glm(session: Session, tenant_id: str, as_of_date: str):
    series_lst = session.scalars(
        select(DailyActual.series).where(DailyActual.tenant_id == tenant_id).distinct()
    ).all()

    if not series_lst:
        return

    as_of_date = date.fromisoformat(as_of_date)

    for series in series_lst:
        result = train_glm(session, tenant_id, series)

        if result is None:
            continue

        model, columns = result
        quantile_grid = compute_quantile_grid(
            session, tenant_id, ModelVersion.POISSON_GLM.value, as_of_date
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
                model_version=ModelVersion.POISSON_GLM.value,
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

        session.commit()


def backtest(session: Session, tenant_id: str, start_date: str, end_date: str):
    start_date = date.fromisoformat(start_date)
    end_date = date.fromisoformat(end_date)
    for offset in range((end_date - start_date).days + 1):
        current_date = start_date + timedelta(days=offset)
        forecast_seasonal_naive(session, tenant_id, str(current_date))
        forecast_trailing_mean(session, tenant_id, str(current_date))
        forecast_glm(session, tenant_id, str(current_date))
        compute_forecast_metrics(session, tenant_id, str(current_date))
