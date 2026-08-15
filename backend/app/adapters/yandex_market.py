"""Yandex Market Partner API Adapter.

API docs: https://yandex.ru/dev/market/partner/
Auth: Api-Key (recommended) or OAuth (legacy)
Base URL: https://api.partner.market.yandex.ru

Key endpoints:
- POST /v2/campaigns/{campaignId}/stats/orders — order stats
- POST /v2/campaigns/{campaignId}/offers/stocks — stocks
- GET  /v2/campaigns/{campaignId}/offer-prices — prices
- POST /v2/businesses/{businessId}/bids/info — bids/advert info
- POST /v2/reports/united-marketplace-services/generate — finance report

Note: Api-Key is the recommended auth method. OAuth is legacy and may have limited access.
"""

import httpx
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from decimal import Decimal

from app.adapters.base import MarketplaceAdapter
from app.utils.retry import async_retry

logger = logging.getLogger(__name__)


class YandexMarketAdapter(MarketplaceAdapter):
    """Adapter for Yandex Market Partner API."""

    BASE_URL = "https://api.partner.market.yandex.ru"

    def __init__(self, shop_id: str, credentials: Dict[str, Any]):
        super().__init__(shop_id, credentials)
        self.api_key = credentials.get("api_key", "")
        self.oauth_token = credentials.get("oauth_token", "")
        self.business_id = credentials.get("business_id", "")
        self.campaign_id = credentials.get("campaign_id", "")

        # Build headers based on auth method
        if self.api_key:
            self.headers = {
                "Api-Key": self.api_key,
                "Content-Type": "application/json",
            }
            self.auth_method = "api_key"
        elif self.oauth_token:
            self.headers = {
                "Authorization": f"Bearer {self.oauth_token}",
                "Content-Type": "application/json",
            }
            if self.business_id:
                self.headers["X-Business-Id"] = self.business_id
            self.auth_method = "oauth"
        else:
            raise ValueError("Either api_key or oauth_token must be provided in credentials")

    async def authenticate(self) -> bool:
        """Check credentials by getting campaigns list.

        Also auto-detects business_id from campaigns if not provided.
        """
        try:
            data = await self._get("/v2/campaigns", params={"limit": 10})
            if isinstance(data, list) and len(data) > 0:
                # Auto-detect business_id if not set
                if not self.business_id:
                    first_business = data[0].get("business", {})
                    bid = first_business.get("id")
                    if bid:
                        self.business_id = str(bid)
                        logger.info(f"Auto-detected YM Business ID: {self.business_id}")
                return True
            if isinstance(data, dict) and data.get("campaigns"):
                if not self.business_id:
                    first_business = data["campaigns"][0].get("business", {})
                    bid = first_business.get("id")
                    if bid:
                        self.business_id = str(bid)
                        logger.info(f"Auto-detected YM Business ID: {self.business_id}")
                return True
            return False
        except Exception:
            return False

    @async_retry(max_retries=3, base_delay=1.0, max_delay=30.0)
    async def _get(self, endpoint: str, params: Optional[Dict] = None) -> Any:
        """Make GET request to YM API with retry."""
        url = f"{self.BASE_URL}{endpoint}"
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self.headers, params=params, timeout=30.0)
            response.raise_for_status()
            return response.json()

    @async_retry(max_retries=3, base_delay=1.0, max_delay=30.0)
    async def _post(self, endpoint: str, data: Optional[Dict] = None) -> Any:
        """Make POST request to YM API with retry."""
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
            order_status = item.get("status", "").upper()
            order_date = item.get("creationDate", "")
            try:
                sale_date = datetime.fromisoformat(order_date.replace("Z", "+00:00"))
            except (ValueError, AttributeError):
                sale_date = datetime.utcnow()

            order_id = str(item.get("id", ""))
            for product in item.get("items", []):
                shop_sku = str(product.get("shopSku", ""))
                prices = product.get("prices", []) or []
                buyer_price = next(
                    (p.get("costPerItem") for p in prices if p.get("type") == "BUYER"), None
                )
                marketplace_price = next(
                    (p.get("costPerItem") for p in prices if p.get("type") == "MARKETPLACE"), None
                )
                quantity = int(product.get("count", 1) or 1)

                sales.append({
                    "date": sale_date,
                    "external_sku": shop_sku,
                    "external_id": order_id,
                    "name": product.get("offerName") or product.get("name") or shop_sku,
                    "quantity": quantity,
                    "price": Decimal(str(marketplace_price or buyer_price or 0)),
                    "revenue": Decimal(str((buyer_price or marketplace_price or 0) * quantity)),
                    "commission": Decimal("0"),
                    "logistics": Decimal("0"),
                    "storage": Decimal("0"),
                    "advertising": Decimal("0"),
                    "returns": Decimal("0"),
                    "other": Decimal("0"),
                    "is_return": order_status in ("CANCELLED", "CANCELLED_BY_CUSTOMER", "RETURNED", "PARTIALLY_RETURNED"),
                })
        return sales

    async def get_orders(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Get orders from Yandex Market.

        Same endpoint as sales but with different parsing.
        """
        return await self.get_sales(date_from, date_to)

    async def get_stocks(self) -> List[Dict[str, Any]]:
        """Get stock levels from Yandex Market.

        Uses /v2/campaigns/{campaignId}/offers/stocks
        """
        data = await self._post(
            f"/v2/campaigns/{self.campaign_id}/offers/stocks",
            {"limit": 200},
        )

        stocks = []
        offers = data.get("result", {}).get("offers", []) if isinstance(data, dict) else []
        for offer in offers:
            stock_entries = offer.get("stocks", [])
            total_qty = sum(s.get("count", 0) for s in stock_entries)
            shop_sku = str(offer.get("offerId", "") or offer.get("id", "") or offer.get("shopSku", ""))
            stocks.append({
                "external_sku": shop_sku,
                "external_id": str(offer.get("marketSku", "") or offer.get("shopSku", "")),
                "name": offer.get("name") or offer.get("offerName") or shop_sku,
                "warehouse": stock_entries[0].get("warehouseName", "YM") if stock_entries else "YM",
                "quantity": total_qty,
                "in_way": 0,
            })
        return stocks

    async def get_adverts(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Get advertising/bids data from Yandex Market.

        Uses /v2/businesses/{businessId}/bids/info
        """
        if not self.business_id:
            logger.warning("YM Business ID not available, skipping adverts")
            return []

        data = await self._post(
            f"/v2/businesses/{self.business_id}/bids/info",
            {},
        )

        adverts = []
        for item in data.get("result", {}).get("offers", []):
            adverts.append({
                "date": datetime.now(timezone.utc),
                "campaign_id": "",
                "external_sku": str(item.get("offerId", "")),
                "views": 0,
                "clicks": 0,
                "ctr": Decimal("0"),
                "cpc": Decimal(str(item.get("bid", 0) or 0)),
                "spend": Decimal("0"),
                "orders": 0,
                "cr": Decimal("0"),
            })
        return adverts

    async def get_product_info(self, offer_ids: List[str]) -> Dict[str, str]:
        """Fetch product names from Yandex Market catalog.

        Uses /v2/businesses/{businessId}/offer-mappings
        Returns a mapping offerId -> name.
        """
        if not self.business_id or not offer_ids:
            return {}

        try:
            data = await self._post(
                f"/v2/businesses/{self.business_id}/offer-mappings",
                {"offerIds": list(set(offer_ids))},
            )

            result = {}
            for item in data.get("result", {}).get("offerMappings", []):
                offer = item.get("offer", {}) or {}
                mapping = item.get("mapping", {}) or {}
                offer_id = str(offer.get("offerId", ""))
                if not offer_id:
                    continue
                name = (
                    offer.get("name")
                    or mapping.get("marketSkuName")
                    or offer_id
                )
                result[offer_id] = name
            return result
        except Exception as e:
            logger.warning("YM offer-mappings failed: %s", e)
            return {}

    async def get_prices(self) -> List[Dict[str, Any]]:
        """Get current prices from Yandex Market.

        Uses /v2/campaigns/{campaignId}/offer-prices
        """
        data = await self._get(
            f"/v2/campaigns/{self.campaign_id}/offer-prices",
            params={"limit": 200},
        )

        prices = []
        offers = data.get("result", {}).get("offers", []) if isinstance(data, dict) else []
        for item in offers:
            price_info = item.get("price", {})
            price_val = price_info.get("value", 0) if isinstance(price_info, dict) else 0
            shop_sku = str(item.get("offerId", "") or item.get("id", ""))
            prices.append({
                "external_sku": shop_sku,
                "external_id": str(item.get("marketSku", "") or item.get("shopSku", "")),
                "name": item.get("offerName") or item.get("name") or shop_sku,
                "price": Decimal(str(price_val or 0)),
                "discount": 0,
            })
        return prices

    async def get_finance_report(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Get finance report from Yandex Market.

        Uses /v2/reports/united-marketplace-services/generate
        This is async — first request generates report, then we poll for status.
        For simplicity, we return empty list (full implementation requires polling).
        """
        if not self.business_id:
            logger.warning("YM Business ID not available, skipping finance report")
            return []

        gen_response = await self._post(
            "/v2/reports/united-marketplace-services/generate",
            {
                "businessId": int(self.business_id) if self.business_id.isdigit() else 0,
                "dateFrom": date_from.strftime("%Y-%m-%d"),
                "dateTo": date_to.strftime("%Y-%m-%d"),
            },
        )

        report_id = gen_response.get("result", {}).get("reportId")
        if report_id:
            logger.info(f"YM finance report generation started: {report_id}")

        # TODO: Implement polling with GET /v2/reports/info/{reportId}
        return []
