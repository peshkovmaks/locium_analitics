"""Ozon Seller API Adapter.

Verified working endpoints (August 2026):
- POST /v2/warehouse/list — authentication
- POST /v5/product/info/prices — prices
- POST /v2/posting/fbo/list — FBO orders
- POST /v3/posting/fbs/list — FBS orders
- POST /v1/analytics/data — sales analytics

Note: /v3/product/info/stocks returns 404 — disabled.
Note: Advertising requires Performance API (OAuth) — disabled.
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
        """Check credentials by getting warehouse list."""
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
            response = await client.post(
                url, headers=self.headers, json=data or {}, timeout=30.0
            )
            response.raise_for_status()
            return response.json()

    async def get_sales(
        self, date_from: datetime, date_to: datetime
    ) -> List[Dict[str, Any]]:
        """Get sales data from Ozon analytics."""
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
            sales.append(
                {
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
                }
            )
        return sales

    async def get_orders(
        self, date_from: datetime, date_to: datetime
    ) -> List[Dict[str, Any]]:
        """Get orders from Ozon (FBO + FBS)."""
        orders = []

        # FBO orders
        fbo_data = await self._post(
            "/v2/posting/fbo/list",
            {
                "dir": "ASC",
                "filter": {
                    "since": date_from.astimezone(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "to": date_to.astimezone(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z"),
                },
                "limit": 1000,
                "offset": 0,
                "with": {"analytics_data": True},
            },
        )

        for item in fbo_data.get("result", []):
            for product in item.get("products", []):
                orders.append(
                    {
                        "date": datetime.fromisoformat(
                            item.get("created_at", "").replace("Z", "+00:00")
                        ),
                        "external_sku": str(product.get("offer_id", "")),
                        "external_id": str(product.get("sku", "")),
                        "quantity": product.get("quantity", 1),
                        "price": Decimal(str(product.get("price", "0") or "0")),
                        "status": item.get("status", ""),
                    }
                )

        # FBS orders
        fbs_data = await self._post(
            "/v3/posting/fbs/list",
            {
                "dir": "ASC",
                "filter": {
                    "since": date_from.astimezone(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "to": date_to.astimezone(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z"),
                },
                "limit": 1000,
                "offset": 0,
                "with": {"analytics_data": True},
            },
        )

        for item in fbs_data.get("result", {}).get("postings", []):
            for product in item.get("products", []):
                orders.append(
                    {
                        "date": datetime.fromisoformat(
                            item.get("created_at", "").replace("Z", "+00:00")
                        ),
                        "external_sku": str(product.get("offer_id", "")),
                        "external_id": str(product.get("sku", "")),
                        "quantity": product.get("quantity", 1),
                        "price": Decimal(str(product.get("price", "0") or "0")),
                        "status": item.get("status", ""),
                    }
                )

        return orders

    async def get_stocks(self) -> List[Dict[str, Any]]:
        """STOCKS DISABLED: Ozon stocks endpoint returns 404.
        Returns empty list to avoid breaking sync."""
        logger.info("Ozon stocks skipped: endpoint unavailable (404)")
        return []

    async def get_adverts(
        self, date_from: datetime, date_to: datetime
    ) -> List[Dict[str, Any]]:
        """ADVERTS DISABLED: Requires Performance API (OAuth).
        Returns empty list."""
        logger.info("Ozon adverts skipped: requires Performance API (OAuth)")
        return []

    async def get_prices(self) -> List[Dict[str, Any]]:
        """Get current prices from Ozon (v5)."""
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
            price_val = (
                price_info.get("price", 0)
                if isinstance(price_info, dict)
                else item.get("price", 0)
            )
            prices.append(
                {
                    "external_sku": str(item.get("offer_id", "")),
                    "external_id": str(item.get("sku", "")),
                    "price": Decimal(str(price_val or 0)),
                    "discount": item.get("discount", 0),
                }
            )
        return prices

    async def get_finance_report(
        self, date_from: datetime, date_to: datetime
    ) -> List[Dict[str, Any]]:
        """Get finance realization report from Ozon."""
        data = await self._post(
            "/v1/finance/realization",
            {
                "date_from": date_from.strftime("%Y-%m-%d"),
                "date_to": date_to.strftime("%Y-%m-%d"),
            },
        )

        reports = []
        for item in data.get("realization", []):
            reports.append(
                {
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
                }
            )
        return reports
