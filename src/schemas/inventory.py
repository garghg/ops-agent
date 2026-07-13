from enum import Enum
from pydantic import BaseModel
from uuid import UUID
from decimal import Decimal


class InventoryTransactionType(str, Enum):
    RESTOCK = "restock"
    USAGE = "usage"
    WASTE = "waste"
    ADJUSTMENT_ADD = "adjustment_add"
    ADJUSTMENT_SUB = "adjustment_sub"


class InventoryEventPayload(BaseModel):
    item_id: UUID
    quantity: Decimal
    transaction_type: InventoryTransactionType
    note: str | None = None
