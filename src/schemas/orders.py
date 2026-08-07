from enum import Enum


class OrderBy(str, Enum):
    SYSTEM = "system"
    OWNER = "owner"


class PredictionMode(str, Enum):
    FORECAST = "forecast"
    PAR = "par"