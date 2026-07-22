import httpx
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from src.db.session import SessionLocal
from src.db.models import Tenant, WeatherObservation
from sqlalchemy.dialects.postgresql import insert
from datetime import date, timedelta


def geocode(city: str) -> tuple[float, float, str]:
    response = httpx.get(
        "https://geocoding-api.open-meteo.com/v1/search",
        params={"name": city, "count": 1},
    )
    response.raise_for_status()

    data = response.json()["results"][0]
    return [data["longitude"], data["latitude"], data["name"]]


def _fetch_weather(url: str, params: dict) -> list[dict]:
    response = httpx.get(url, params=params)
    response.raise_for_status()

    data = response.json()["daily"]
    results = []
    for i, date_str in enumerate(data["time"]):
        results.append(
            {
                "observation_date": date_str,
                "max_temp_c": data["temperature_2m_max"][i],
                "min_temp_c": data["temperature_2m_min"][i],
                "precipitation_mm": data["precipitation_sum"][i],
            }
        )
    return results


def fetch_forecast(
    latitude: float, longitude: float, forecast_days: int = 14
) -> list[dict]:
    return _fetch_weather(
        "https://api.open-meteo.com/v1/forecast",
        {
            "latitude": latitude,
            "longitude": longitude,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
            "forecast_days": forecast_days,
        },
    )


def fetch_actuals(
    latitude: float, longitude: float, start_date: str, end_date: str
) -> list[dict]:
    return _fetch_weather(
        "https://archive-api.open-meteo.com/v1/archive",
        {
            "latitude": latitude,
            "longitude": longitude,
            "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum",
            "start_date": start_date,
            "end_date": end_date,
        },
    )


def update_db(session: Session, weather_list: list[dict], tenant_id: str, source: str):
    for data in weather_list:
        stmt = insert(WeatherObservation).values(
            tenant_id=tenant_id,
            observation_date=data["observation_date"],
            source=source,
            max_temp_c=data["max_temp_c"],
            min_temp_c=data["min_temp_c"],
            precipitation_mm=data["precipitation_mm"],
        )
        stmt = stmt.on_conflict_do_update(
            constraint="weather_observations_tenant_date_source_key",
            set_={
                "max_temp_c": stmt.excluded.max_temp_c,
                "min_temp_c": stmt.excluded.min_temp_c,
                "precipitation_mm": stmt.excluded.precipitation_mm,
                "fetched_at": func.now(),
            },
        )
        session.execute(stmt)
    session.commit()


def collect_weather():
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    with SessionLocal() as session:
        tenants = session.scalars(
            select(Tenant).where(
                Tenant.latitude.is_not(None),
                Tenant.longitude.is_not(None),
            )
        ).all()

        for tenant in tenants:
            forecasts = fetch_forecast(float(tenant.latitude), float(tenant.longitude))
            update_db(session, forecasts, tenant.id, "forecast")

            actuals = fetch_actuals(
                float(tenant.latitude), float(tenant.longitude), yesterday, yesterday
            )
            update_db(session, actuals, tenant.id, "actual")
