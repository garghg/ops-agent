from enum import Enum


class EmailStatus(str, Enum):
    SENT = "sent"
    PENDING = "pending"
    FAILED = "failed"
