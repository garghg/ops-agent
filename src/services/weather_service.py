import httpx


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
