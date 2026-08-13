"""Ozon Seller API Adapter.

API docs: https://api-seller.ozon.ru/
Auth: Client-Id + Api-Key headers
Base URL: https://api-seller.ozon.ru

Key endpoints:
- POST /v1/analytics/data — sales analytics
- POST /v3/product/info/stocks-by-warehouse/fbs — stocks
- POST /v2/posting/fbo/list — FBO orders
- POST /v3/posting/fbs/list — FBS orders
- POST /v1/finance/realization — finance report
- POST /v1/campaign/list — advert campaigns
- POST /v1/campaign/product/info — advert stats

Note: As of July 2026, /v3/finance/transaction/list is deprecated.
"""

import httpx
from datetime import datetime
from typing import List, Dict, Any, Optional
from decimal import Decimal

from app.adapters.base import MarketplaceAdapter


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
                    f"{self.BASE_URL}/v1/warehouse/list",
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
                    "since": date_from.isoformat() + "Z",
                    "to": date_to.isoformat() + "Z",
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
                    "price": Decimal(str(product.get("price", "0"))),
                    "status": item.get("status", ""),
                })

        # FBS orders
        fbs_data = await self._post(
            "/v3/posting/fbs/list",
            {
                "dir": "ASC",
                "filter": {
                    "since": date_from.isoformat() + "Z",
                    "to": date_to.isoformat() + "Z",
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
                    "price": Decimal(str(product.get("price", "0"))),
                    "status": item.get("status", ""),
                })

        return orders

    async def get_stocks(self) -> List[Dict[str, Any]]:
        """Get stock levels from Ozon."""
        data = await self._post(
            "/v3/product/info/stocks-by-warehouse/fbs",
            {"limit": 1000, "offset": 0},
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
        return stocks

    async def get_adverts(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Get advertising data from Ozon.

        Ozon advert structure is different from WB.
        """
        # Get campaigns
        campaigns = await self._post("/v1/campaign/list", {})

        adverts = []
        for campaign in campaigns.get("campaigns", []):
            camp_id = campaign.get("campaignId")

            # Get campaign products/stats
            try:
                stats = await self._post(
                    "/v1/campaign/product/info",
                    {"campaignId": camp_id},
                )

                for product in stats.get("products", []):
                    adverts.append({
                        "date": datetime.utcnow(),  # Ozon doesn't provide daily breakdown easily
                        "campaign_id": str(camp_id),
                        "external_sku": str(product.get("offerId", "")),
                        "views": product.get("views", 0),
                        "clicks": product.get("clicks", 0),
                        "ctr": Decimal(str(product.get("ctr", 0))),
                        "cpc": Decimal(str(product.get("cpc", 0))),
                        "spend": Decimal(str(product.get("moneySpent", 0))),
                        "orders": product.get("orders", 0),
                        "cr": Decimal(str(product.get("cr", 0))),
                    })
            except Exception:
                continue

        return adverts

    async def get_prices(self) -> List[Dict[str, Any]]:
        """Get current prices from Ozon."""
        data = await self._post(
            "/v4/product/info/prices",
            {"limit": 1000, "offset": 0},
        )

        prices = []
        for item in data.get("items", []):
            prices.append({
                "external_sku": str(item.get("offer_id", "")),
                "external_id": str(item.get("sku", "")),
                "price": Decimal(str(item.get("price", 0))),
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
