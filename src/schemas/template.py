from pydantic import BaseModel, ConfigDict


class ScheduleConfig(BaseModel):
    opening_hour: int = 10
    opening_min: int = 0
    closing_hour: int = 18
    closing_min: int = 0
    schedule_gen_day_of_week: str = "wed"
    schedule_gen_hour: int = 9
    schedule_gen_minute: int = 0
    poll_interval_seconds: int = 10800
    service_rate: int = 10
    min_staffing: int = 2
    slot_length_minutes: int = 15


class AlertThresholds(BaseModel):
    void_rate: float = 0.05
    refund_rate: float = 0.05
    discount_rate: float = 0.10


class AnomalyConfig(BaseModel):
    cooldown_hours: int = 48
    checkpoint_hour: int = 14
    tier1_types: list[str] = ["stale_heartbeat"]


class OrderingConfig(BaseModel):
    default_service_level: float = 0.95
    category_service_levels: dict[str, float] = {}
    max_order_value: float = 500.0
    max_daily_spend: float = 1500.0
    max_weekly_spend: float = 5000.0
    novelty_threshold: float = 1.5

class LearningConfig(BaseModel):
    forecast_bias_half_life: int = 7
    forecast_bias_clamp_low: float = 0.75
    forecast_bias_clamp_high: float = 1.30
    order_edit_half_life: int = 5
    order_edit_clamp_low: float = 0.80
    order_edit_clamp_high: float = 1.25
    shrinkage_half_life: int = 4
    shrinkage_clamp_low: float = 0.0
    shrinkage_clamp_high: float = 0.30
    clamp_alert_threshold: int = 5

class TemplateConfig(BaseModel):
    schedule: ScheduleConfig = ScheduleConfig()
    alerts: AlertThresholds = AlertThresholds()
    ordering: OrderingConfig = OrderingConfig()
    anomalies: AnomalyConfig = AnomalyConfig()
    learning: LearningConfig = LearningConfig()
    model_config = ConfigDict(extra="forbid")
