"""Ozon Seller API Adapter.

API docs: https://api-seller.ozon.ru/
Auth: Client-Id + Api-Key headers
Base URL: https://api-seller.ozon.ru

Key endpoints:
- POST /v1/analytics/data — sales analytics
- POST /v3/product/info/stocks — stocks (stocks-by-warehouse deprecated)
- POST /v2/posting/fbo/list — FBO orders
- POST /v3/posting/fbs/list — FBS orders
- POST /v1/finance/realization — finance report
- POST /v5/product/info/prices — prices (v4 deprecated)

Note: Advertising campaigns require Ozon Performance API (api-performance.ozon.ru)
with OAuth, separate from Seller API. get_adverts() returns empty list.

Note: As of July 2026, /v3/finance/transaction/list is deprecated.
"""

import httpx
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from decimal import Decimal

from app.adapters.base import MarketplaceAdapter
from app.utils.retry import async_retry

logger = logging.getLogger(__name__)


class OzonAdapter(MarketplaceAdapter):
    """Adapter for Ozon Seller API."""

    BASE_URL = "https://api-seller.ozon.ru"

    def __init__(self, shop_id: str, credentials: Dict[str, Any]):
        super().__init__(shop_id, credentials)
        self.client_id = credentials.get("client_id", "")
        self.api_key = credentials.get("api_key", "")
        self.headers = {
            "Client-Id": self.client_id,
            "Api-Key": self.api_key,
            "Content-Type": "application/json",
        }

    async def authenticate(self) -> bool:
        """Check credentials by getting warehouse list.

        Uses /v2/warehouse/list since /v1/warehouse/list was disabled by Ozon
        on March 20, 2026 (extended to April 7, 2026).
        """
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.BASE_URL}/v2/warehouse/list",
                    headers=self.headers,
                    json={},
                    timeout=10.0,
                )
                return response.status_code == 200
        except Exception:
            return False

    @async_retry(max_retries=3, base_delay=1.0, max_delay=30.0)
    async def _post(self, endpoint: str, data: Optional[Dict] = None) -> Any:
        """Make POST request to Ozon API with retry."""
        url = f"{self.BASE_URL}{endpoint}"
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=self.headers, json=data or {}, timeout=30.0)
            response.raise_for_status()
            return response.json()

    async def get_sales(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Get sales data from Ozon analytics.

        Uses /v1/analytics/data with 'sales' metrics.
        """
        data = await self._post(
            "/v1/analytics/data",
            {
                "date_from": date_from.strftime("%Y-%m-%d"),
                "date_to": date_to.strftime("%Y-%m-%d"),
                "metrics": ["ordered_units", "revenue", "cancelled_units"],
                "dimension": ["sku", "day"],
                "filters": [],
                "sort": [{"key": "revenue", "order": "DESC"}],
                "limit": 1000,
            },
        )

        sales = []
        for item in data.get("data", []):
            dimensions = item.get("dimensions", [{}])[0]
            metrics = item.get("metrics", [0, 0, 0])

            sales.append({
                "date": datetime.strptime(dimensions.get("day", ""), "%Y-%m-%d"),
                "external_sku": str(dimensions.get("sku", "")),
                "external_id": str(dimensions.get("sku", "")),
                "quantity": int(metrics[0] or 0),
                "price": Decimal("0"),
                "revenue": Decimal(str(metrics[1] or 0)),
                "commission": Decimal("0"),
                "logistics": Decimal("0"),
                "storage": Decimal("0"),
                "advertising": Decimal("0"),
                "returns": Decimal("0"),
                "other": Decimal("0"),
                "is_return": False,
            })
        return sales

    async def get_orders(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Get orders from Ozon.

        Combines FBO and FBS orders.
        """
        orders = []

        # FBO orders
        fbo_data = await self._post(
            "/v2/posting/fbo/list",
            {
                "dir": "ASC",
                "filter": {
                    "since": date_from.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "to": date_to.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                },
                "limit": 1000,
                "offset": 0,
                "with": {"analytics_data": True},
            },
        )

        for item in fbo_data.get("result", []):
            for product in item.get("products", []):
                orders.append({
                    "date": datetime.fromisoformat(item.get("created_at", "").replace("Z", "+00:00")),
                    "external_sku": str(product.get("offer_id", "")),
                    "external_id": str(product.get("sku", "")),
                    "quantity": product.get("quantity", 1),
                    "price": Decimal(str(product.get("price", "0") or "0")),
                    "status": item.get("status", ""),
                })

        # FBS orders
        fbs_data = await self._post(
            "/v3/posting/fbs/list",
            {
                "dir": "ASC",
                "filter": {
                    "since": date_from.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                    "to": date_to.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                },
                "limit": 1000,
                "offset": 0,
                "with": {"analytics_data": True},
            },
        )

        for item in fbs_data.get("result", {}).get("postings", []):
            for product in item.get("products", []):
                orders.append({
                    "date": datetime.fromisoformat(item.get("created_at", "").replace("Z", "+00:00")),
                    "external_sku": str(product.get("offer_id", "")),
                    "external_id": str(product.get("sku", "")),
                    "quantity": product.get("quantity", 1),
                    "price": Decimal(str(product.get("price", "0") or "0")),
                    "status": item.get("status", ""),
                })

        return orders

    async def get_stocks(self) -> List[Dict[str, Any]]:
        """Get stock levels from Ozon.

        Uses /v3/product/info/stocks since stocks-by-warehouse endpoints
        are either deprecated (/v1, /v3) or undocumented (/v2).

        Returns aggregated stock quantities across all warehouses.
        """
        data = await self._post(
            "/v3/product/info/stocks",
            {"page": 1, "page_size": 1000},
        )

        stocks = []
        for item in data.get("items", []):
            stock_entries = item.get("stocks", [])
            total_present = sum(s.get("present", 0) for s in stock_entries)
            total_reserved = sum(s.get("reserved", 0) for s in stock_entries)

            stocks.append({
                "external_sku": str(item.get("offer_id", "")),
                "external_id": str(item.get("product_id", "")),
                "warehouse": stock_entries[0].get("warehouse_name", "FBS") if stock_entries else "FBS",
                "quantity": total_present,
                "in_way": total_reserved,
            })
        return stocks

    async def get_adverts(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Get advertising data from Ozon.

        WARNING: Ozon Seller API (api-seller.ozon.ru) does NOT provide advertising
        campaign endpoints. Ads are managed via Ozon Performance API 
        (api-performance.ozon.ru) which requires OAuth authentication.

        Until then, this method returns an empty list.
        """
        logger.warning(
            "Ozon adverts skipped: requires Performance API (OAuth). "
            "Seller API does not provide campaign endpoints."
        )
        return []

    async def get_prices(self) -> List[Dict[str, Any]]:
        """Get current prices from Ozon.

        Uses /v5/product/info/prices since /v4 was deprecated.
        """
        data = await self._post(
            "/v5/product/info/prices",
            {
                "filter": {"visibility": "ALL"},
                "limit": 1000,
                "cursor": "",
            },
        )

        prices = []
        for item in data.get("items", []):
            price_info = item.get("price", {})
            if isinstance(price_info, dict):
                price_val = price_info.get("price", 0)
            else:
                price_val = item.get("price", 0)
            prices.append({
                "external_sku": str(item.get("offer_id", "")),
                "external_id": str(item.get("sku", "")),
                "price": Decimal(str(price_val or 0)),
                "discount": item.get("discount", 0),
            })
        return prices

    async def get_finance_report(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Get finance realization report from Ozon.

        Note: /v3/finance/transaction/list is deprecated as of July 2026.
        Using /v1/finance/realization instead.
        """
        data = await self._post(
            "/v1/finance/realization",
            {
                "date_from": date_from.strftime("%Y-%m-%d"),
                "date_to": date_to.strftime("%Y-%m-%d"),
            },
        )

        reports = []
        for item in data.get("realization", []):
            reports.append({
                "date": datetime.strptime(item.get("date", ""), "%Y-%m-%d"),
                "external_sku": str(item.get("offer_id", "")),
                "external_id": str(item.get("sku", "")),
                "quantity": item.get("quantity", 0),
                "price": Decimal(str(item.get("price", 0))),
                "revenue": Decimal(str(item.get("amount", 0))),
                "commission": Decimal(str(item.get("commission", 0))),
                "logistics": Decimal(str(item.get("logistics", 0))),
                "storage": Decimal(str(item.get("storage", 0))),
                "returns": Decimal(str(item.get("returns", 0))),
                "other": Decimal(str(item.get("other", 0))),
            })
        return reports
