from unittest.mock import patch, MagicMock
from decimal import Decimal
from sqlalchemy import select
from src.db.models import Tenant, WeatherObservation
from src.services.weather_service import collect_weather

FAKE_FORECAST_RESPONSE = {
    "daily": {
        "time": ["2026-07-22", "2026-07-23", "2026-07-24"],
        "temperature_2m_max": [28.0, 25.5, 30.2],
        "temperature_2m_min": [15.0, 14.2, 17.8],
        "precipitation_sum": [0.0, 5.2, 0.0],
    }
}

FAKE_ACTUALS_RESPONSE = {
    "daily": {
        "time": ["2026-07-21"],
        "temperature_2m_max": [27.3],
        "temperature_2m_min": [14.9],
        "precipitation_sum": [1.1],
    }
}


def mock_httpx_get(url, params=None):
    response = MagicMock()
    response.raise_for_status = MagicMock()
    if "archive" in url:
        response.json.return_value = FAKE_ACTUALS_RESPONSE
    else:
        response.json.return_value = FAKE_FORECAST_RESPONSE
    return response


@patch("src.services.weather_service.httpx.get", side_effect=mock_httpx_get)
def test_collect_weather(_, seeded_db):
    tenant = seeded_db.scalars(select(Tenant)).first()
    tenant.latitude = Decimal("49.282700")
    tenant.longitude = Decimal("-123.120700")
    seeded_db.commit()

    collect_weather()

    seeded_db.expire_all()

    forecasts = seeded_db.scalars(
        select(WeatherObservation).where(
            WeatherObservation.tenant_id == tenant.id,
            WeatherObservation.source == "forecast",
        )
    ).all()
    assert len(forecasts) == 3, f"Expected 3 forecast rows, got {len(forecasts)}"
    assert forecasts[0].max_temp_c == Decimal("28.00")

    actuals = seeded_db.scalars(
        select(WeatherObservation).where(
            WeatherObservation.tenant_id == tenant.id,
            WeatherObservation.source == "actual",
        )
    ).all()
    assert len(actuals) == 1, f"Expected 1 actual row, got {len(actuals)}"
    assert actuals[0].max_temp_c == Decimal("27.30")
    assert actuals[0].precipitation_mm == Decimal("1.10")