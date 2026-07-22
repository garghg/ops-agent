from enum import Enum


class EventCategory(str, Enum):
    INVENTORY = "inventory"
    PROCUREMENT = "procurement"
    WORKFORCE = "workforce"
    SYSTEM = "system"
    SALES = "sales"


class SalesEventType(str, Enum):
    SALE_COMPLETED = "sale_completed"


class SystemEventType(str, Enum):
    DAY_OPENED = "day_opened"
    DAY_CLOSED = "day_closed"


class WorkforceEventType(str, Enum):
    SCHEDULE_GENERATION_REQUESTED = "schedule_generation_requested"


class ConsumerGroup(str, Enum):
    STOCK_UPDATER = "stock_updater"
    BOM_CONSUMER = "bom_consumer"
    WEATHER_CONSUMER = "weather_consumer"


class InventoryEventType(str, Enum):
    BELOW_REORDER_POINT = "below_reorder_point"
    BOM_DEPLETION = "bom_depletion"
