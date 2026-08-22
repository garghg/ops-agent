from decimal import Decimal
from enum import Enum
from uuid import UUID

from pydantic import BaseModel


class InventoryTransactionType(str, Enum):
    RESTOCK = "restock"
    USAGE = "usage"
    ADJUSTMENT_ADD = "adjustment_add"
    ADJUSTMENT_SUB = "adjustment_sub"
    SHRINKAGE = "shrinkage"

SUBTRACT_TYPES = {
    InventoryTransactionType.USAGE,
    InventoryTransactionType.ADJUSTMENT_SUB,
    InventoryTransactionType.SHRINKAGE,
}

class InventoryEventPayload(BaseModel):
    item_id: UUID
    quantity: Decimal
    transaction_type: InventoryTransactionType
    note: str | None = None
    source_key: str | None = None