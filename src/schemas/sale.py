from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel


class SaleTransactionType(str, Enum):
    SALE = "sale"
    VOID = "void"
    REFUND = "refund"

class SaleLineItem(BaseModel):
    item_name: str
    modifiers: list[str] = []
    quantity: int = 1
    unit_price: Decimal


class SaleEvent(BaseModel):
    external_transaction_id: str
    source: str
    timestamp: datetime
    line_items: list[SaleLineItem]
    total: Decimal
    payment_method: str
    transaction_type: str = SaleTransactionType.SALE
    discount_amount: Decimal = Decimal(0)