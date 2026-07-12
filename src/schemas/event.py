from enum import Enum

class EventCategory(str, Enum):
    INVENTORY = "inventory"
    PROCUREMENT = "procurement"

class ConsumerGroup(str, Enum):
    STOCK_UPDATER = "stock_updater"