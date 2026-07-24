import enum


class POStatus(str, enum.Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    SENT = "sent"
    CONFIRMED = "confirmed"
    DELIVERED = "delivered"
    RECEIVED = "received"
    CANCELLED = "cancelled"
