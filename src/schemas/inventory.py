from decimal import Decimal
from enum import Enum
from uuid import UUID

from pydantic import BaseModel


class InventoryTransactionType(str, Enum):
    RESTOCK = "restock"
    USAGE = "usage"
    WASTE = "waste"
    ADJUSTMENT_ADD = "adjustment_add"
    ADJUSTMENT_SUB = "adjustment_sub"

SUBTRACT_TYPES = {
    InventoryTransactionType.USAGE,
    InventoryTransactionType.WASTE,
    InventoryTransactionType.ADJUSTMENT_SUB,
}

class InventoryEventPayload(BaseModel):
    item_id: UUID
    quantity: Decimal
    transaction_type: InventoryTransactionType
    note: str | None = None