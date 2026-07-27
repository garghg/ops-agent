from abc import ABC, abstractmethod

from src.schemas.sale import SaleEvent


class POSAdapter(ABC):
    @abstractmethod
    def fetch_sales(self) -> list[SaleEvent]:
        ...