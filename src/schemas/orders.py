from enum import Enum


class OrderBy(str, Enum):
    SYSTEM = "system"
    OWNER = "owner"
