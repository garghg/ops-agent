import enum


class POStatus(str, enum.Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    SENT = "sent"
    CONFIRMED = "confirmed"
    RECEIVED = "received"
    CANCELLED = "cancelled"