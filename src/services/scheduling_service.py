from datetime import date, timedelta
from math import ceil

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.models import Forecast, IntradayProfile, ModelRegistry
from src.schemas.forecast import ForecastSeries
from src.schemas.models import ModelVersion
from src.services.config_services import resolve_config


def required_per_hour(session: Session, tenant_id: str, week_start: date):
    champion = session.scalar(
        select(ModelRegistry.active_version)
        .where(ModelRegistry.tenant_id == tenant_id)
    )
    
    if not champion:
        champion = ModelVersion.POISSON_GLM.value
    
    forecasts = session.scalars(
        select(Forecast)
        .where(Forecast.tenant_id == tenant_id)
        .where(Forecast.target_date >= week_start)
        .where(Forecast.target_date <= week_start + timedelta(days=7))
        .where(Forecast.model_version == champion)
        .where(Forecast.series == ForecastSeries.TOTAL_UNITS)
        .order_by(Forecast.forecast_date.desc())
    ).all()

    if not forecasts:
        return

    latest_demand_date = session.scalar(
        select(IntradayProfile.as_of_date)
        .where(IntradayProfile.tenant_id == tenant_id)
        .order_by(IntradayProfile.as_of_date.desc())
        .limit(1)
    )
    
    if not latest_demand_date:
        return
    
    demands = session.scalars(
        select(IntradayProfile)
        .where(IntradayProfile.tenant_id == tenant_id)
        .where(IntradayProfile.as_of_date == latest_demand_date)
    ).all()

    if not demands:
        return

    forecast_map = {}
    for f in forecasts:
        if f.target_date not in forecast_map:
            forecast_map[f.target_date] = f.point_estimate
    demand_map = {(d.day_of_week, d.hour): d.fraction for d in demands}

    config = resolve_config(tenant_id, session)
    open_hour = config.schedule.opening_hour
    close_hour = config.schedule.closing_hour
    min_staffing = config.schedule.min_staffing
    service_rate = config.schedule.service_rate
    required = {}
    for d in range(7):
        day = week_start + timedelta(days=d)
        forecast = forecast_map.get(day, 0)
        weekday = day.weekday()
        for h in range(open_hour, close_hour):
            fraction = demand_map.get((weekday, h), 0)
            required[(day, h)] = max(
                ceil(forecast * fraction / service_rate), min_staffing
            )
    
    return required