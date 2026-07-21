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


class TemplateConfig(BaseModel):
    schedule: ScheduleConfig = ScheduleConfig()
    model_config = ConfigDict(extra="forbid")
