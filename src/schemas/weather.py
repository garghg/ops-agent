from enum import Enum


class WeatherSource(str, Enum):
    ACTUAL = "actual"
    FORECAST = "forecast"
