from abc import ABC, abstractmethod
from typing import List, Dict, Any
from datetime import datetime

class MarketplaceAdapter(ABC):
    """Base adapter for all marketplaces."""

    def __init__(self, shop_id: str, credentials: Dict[str, Any]):
        self.shop_id = shop_id
        self.credentials = credentials

    @abstractmethod
    async def authenticate(self) -> bool:
        """Verify credentials are valid."""
        pass

    @abstractmethod
    async def get_sales(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Get sales data for period."""
        pass

    @abstractmethod
    async def get_stocks(self) -> List[Dict[str, Any]]:
        """Get current stock levels."""
        pass

    @abstractmethod
    async def get_adverts(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Get advertising data for period."""
        pass

    @abstractmethod
    async def get_prices(self) -> List[Dict[str, Any]]:
        """Get current prices on marketplace."""
        pass

    @abstractmethod
    async def get_finance_report(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Get detailed finance report."""
        pass


class AdapterFactory:
    """Factory for creating marketplace adapters."""

    _adapters = {}

    @classmethod
    def register(cls, marketplace: str, adapter_class):
        cls._adapters[marketplace] = adapter_class

    @classmethod
    def create(cls, marketplace: str, shop_id: str, credentials: Dict[str, Any]) -> MarketplaceAdapter:
        # Lazy import to avoid circular imports
        if not cls._adapters:
            from app.adapters.wildberries import WildberriesAdapter
            from app.adapters.ozon import OzonAdapter
            from app.adapters.yandex_market import YandexMarketAdapter

            cls._adapters = {
                "wb": WildberriesAdapter,
                "ozon": OzonAdapter,
                "ym": YandexMarketAdapter,
            }

        adapter_class = cls._adapters.get(marketplace)
        if not adapter_class:
            raise ValueError(f"Unknown marketplace: {marketplace}")
        return adapter_class(shop_id, credentials)