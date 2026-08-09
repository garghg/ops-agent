from enum import Enum


class ModelVersion(str, Enum):
    SEASONAL_NAIVE = "seasonal_naive"
    TRAILING_7D_MEAN = "trailing_7d_mean"
    POISSON_GLM = "poisson_glm"
    LIGHTGBM = "light_gbm"