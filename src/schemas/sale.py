from datetime import datetime
from decimal import Decimal
from pydantic import BaseModel


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