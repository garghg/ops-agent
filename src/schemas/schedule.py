from enum import Enum


class ScheduleStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    PUBLISHED = "published"