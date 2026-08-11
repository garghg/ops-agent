from enum import Enum


class ScheduleStatus(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    PUBLISHED = "published"
    FAILED = "failed"
    
class Constraints(str, Enum):
    KEYHOLDER = "keyholder"
    MAX_WEEKLY_HOURS = "max_weekly_hours"
    SHIFT_LENGTH = "shift_length"