"""Ozon Seller API Adapter.

API docs: https://api-seller.ozon.ru/
Auth: Client-Id + Api-Key headers
Base URL: https://api-seller.ozon.ru

Key endpoints:
- POST /v1/analytics/data — sales analytics
- POST /v2/product/info/stocks-by-warehouse/fbs — stocks (v1/v3 deprecated 20.03.2026)
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

    async def _post(self, endpoint: str, data: Optional[Dict] = None) -> Any:
        """Make POST request to Ozon API."""
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
                "price": Decimal("0"),  # Price not directly available, need to get from products
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

        Uses /v2/product/info/stocks-by-warehouse/fbs since /v1/v3 were deprecated
        on March 20, 2026. Tries multiple payload variants because Ozon does not
        publicly document the exact v2 request format.
        """
        # Try multiple payload variants — Ozon hasn't published v2 docs publicly
        variants = [
            {"limit": 1000, "offset": 0},
            {"limit": 1000, "offset": 0, "warehouse_type": "ALL"},
            {"limit": 1000, "offset": 0, "warehouse_ids": []},
            {"cursor": "", "limit": 1000},
            {},
        ]

        last_error = None
        for payload in variants:
            try:
                data = await self._post(
                    "/v2/product/info/stocks-by-warehouse/fbs",
                    payload,
                )

                stocks = []
                for item in data.get("items", []):
                    stocks.append({
                        "external_sku": str(item.get("offer_id", "")),
                        "external_id": str(item.get("sku", "")),
                        "warehouse": item.get("warehouse_name", "FBS"),
                        "quantity": item.get("quantity", 0),
                        "in_way": item.get("in_way_to_client", 0) + item.get("in_way_from_client", 0),
                    })
                logger.info(f"Ozon stocks fetched successfully with payload: {payload}")
                return stocks
            except Exception as e:
                last_error = e
                continue

        logger.error(f"All v2 stocks payload variants failed. Last error: {last_error}")
        # Return empty list rather than crashing the sync
        return []

    async def get_adverts(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Get advertising data from Ozon.

        WARNING: Ozon Seller API (api-seller.ozon.ru) does NOT provide advertising
        campaign endpoints. Ads are managed via Ozon Performance API 
        (api-performance.ozon.ru) which requires OAuth authentication.

        To implement Ozon ads in this dashboard, you need:
        1. Register an OAuth app in Ozon Performance
        2. Obtain access_token via OAuth flow
        3. Use endpoints like /api/client/campaign and /api/client/statistics/list

        Until then, this method returns an empty list.
        """
        logger.warning(
            "Ozon adverts skipped: requires Performance API (OAuth). "
            "Seller API does not provide campaign endpoints. "
            "Implement OAuth flow for api-performance.ozon.ru to enable ads."
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
