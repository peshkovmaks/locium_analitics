"""Yandex Market Partner API Adapter.

API docs: https://yandex.ru/dev/market/partner/
Auth: OAuth 2.0 + X-Business-Id header
Base URL: https://api.partner.market.yandex.ru

Key endpoints:
- POST /v2/campaigns/{campaignId}/stats/orders — order stats
- POST /v2/reports/united-marketplace-services/generate — finance report
- POST /v2/businesses/{businessId}/bids/info — bids/advert info
- POST /v2/businesses/{businessId}/offer-mappings — product catalog
- POST /v2/businesses/{businessId}/offers/stocks — stocks

Note: As of May 18, 2026, basic limits were reduced. Extended limits available with Medium subscription.
"""

import httpx
from datetime import datetime
from typing import List, Dict, Any, Optional
from decimal import Decimal

from app.adapters.base import MarketplaceAdapter


class YandexMarketAdapter(MarketplaceAdapter):
    """Adapter for Yandex Market Partner API."""

    BASE_URL = "https://api.partner.market.yandex.ru"

    def __init__(self, shop_id: str, credentials: Dict[str, Any]):
        super().__init__(shop_id, credentials)
        self.oauth_token = credentials.get("oauth_token", "")
        self.business_id = credentials.get("business_id", "")
        self.campaign_id = credentials.get("campaign_id", "")
        self.headers = {
            "Authorization": f"Bearer {self.oauth_token}",
            "X-Business-Id": self.business_id,
            "Content-Type": "application/json",
        }

    async def authenticate(self) -> bool:
        """Check credentials by getting business info."""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    f"{self.BASE_URL}/v2/businesses/{self.business_id}",
                    headers=self.headers,
                    timeout=10.0,
                )
                return response.status_code == 200
        except Exception:
            return False

    async def _get(self, endpoint: str, params: Optional[Dict] = None) -> Any:
        """Make GET request to YM API."""
        url = f"{self.BASE_URL}{endpoint}"
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self.headers, params=params, timeout=30.0)
            response.raise_for_status()
            return response.json()

    async def _post(self, endpoint: str, data: Optional[Dict] = None) -> Any:
        """Make POST request to YM API."""
        url = f"{self.BASE_URL}{endpoint}"
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=self.headers, json=data or {}, timeout=30.0)
            response.raise_for_status()
            return response.json()

    async def get_sales(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Get order stats from Yandex Market.

        Uses /v2/campaigns/{campaignId}/stats/orders
        """
        data = await self._post(
            f"/v2/campaigns/{self.campaign_id}/stats/orders",
            {
                "dateFrom": date_from.strftime("%Y-%m-%d"),
                "dateTo": date_to.strftime("%Y-%m-%d"),
                "limit": 200,
            },
        )

        sales = []
        for item in data.get("result", {}).get("orders", []):
            for product in item.get("items", []):
                sales.append({
                    "date": datetime.fromisoformat(item.get("creationDate", "").replace("Z", "+00:00")),
                    "external_sku": str(product.get("offerId", "")),
                    "external_id": str(product.get("shopSku", "")),
                    "quantity": product.get("count", 1),
                    "price": Decimal(str(product.get("initialPrice", 0))),
                    "revenue": Decimal(str(product.get("buyerPrice", 0))),
                    "commission": Decimal("0"),
                    "logistics": Decimal("0"),
                    "storage": Decimal("0"),
                    "advertising": Decimal("0"),
                    "returns": Decimal("0"),
                    "other": Decimal("0"),
                    "is_return": item.get("status", "").upper() == "CANCELLED",
                })
        return sales

    async def get_orders(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Get orders from Yandex Market.

        Same endpoint as sales but with different parsing.
        """
        return await self.get_sales(date_from, date_to)

    async def get_stocks(self) -> List[Dict[str, Any]]:
        """Get stock levels from Yandex Market.

        Uses /v2/businesses/{businessId}/offers/stocks
        """
        data = await self._post(
            f"/v2/businesses/{self.business_id}/offers/stocks",
            {"limit": 200},
        )

        stocks = []
        for item in data.get("result", {}).get("warehouses", []):
            for offer in item.get("offers", []):
                stocks.append({
                    "external_sku": str(offer.get("offerId", "")),
                    "external_id": str(offer.get("shopSku", "")),
                    "warehouse": item.get("warehouseName", "YM"),
                    "quantity": offer.get("stock", 0),
                    "in_way": 0,
                })
        return stocks

    async def get_adverts(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Get advertising/bids data from Yandex Market.

        Uses /v2/businesses/{businessId}/bids/info
        """
        # Get bids info
        data = await self._post(
            f"/v2/businesses/{self.business_id}/bids/info",
            {},
        )

        adverts = []
        for item in data.get("result", {}).get("offers", []):
            adverts.append({
                "date": datetime.utcnow(),
                "campaign_id": "",
                "external_sku": str(item.get("offerId", "")),
                "views": 0,  # YM bids API doesn't provide views/clicks directly
                "clicks": 0,
                "ctr": Decimal("0"),
                "cpc": Decimal(str(item.get("bid", 0))),
                "spend": Decimal("0"),
                "orders": 0,
                "cr": Decimal("0"),
            })
        return adverts

    async def get_prices(self) -> List[Dict[str, Any]]:
        """Get current prices from Yandex Market.

        Uses /v2/businesses/{businessId}/offer-mappings
        """
        data = await self._post(
            f"/v2/businesses/{self.business_id}/offer-mappings",
            {"limit": 200},
        )

        prices = []
        for item in data.get("result", {}).get("offerMappings", []):
            offer = item.get("offer", {})
            prices.append({
                "external_sku": str(offer.get("offerId", "")),
                "external_id": str(offer.get("shopSku", "")),
                "price": Decimal(str(offer.get("price", {}).get("value", 0))),
                "discount": 0,
            })
        return prices

    async def get_finance_report(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Get finance report from Yandex Market.

        Uses /v2/reports/united-marketplace-services/generate
        This is async — first request generates report, then we poll for status.
        For simplicity, we'll use a simplified approach.
        """
        # Generate report
        gen_response = await self._post(
            "/v2/reports/united-marketplace-services/generate",
            {
                "businessId": int(self.business_id) if self.business_id.isdigit() else 0,
                "dateFrom": date_from.strftime("%Y-%m-%d"),
                "dateTo": date_to.strftime("%Y-%m-%d"),
            },
        )

        # In real implementation, we would poll for report completion
        # For now, return empty list (to be implemented with polling)
        return []
