"""Ozon Seller API Adapter."""

import httpx
import logging
import asyncio
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from decimal import Decimal

from app.adapters.base import MarketplaceAdapter
from app.utils.retry import async_retry

logger = logging.getLogger(__name__)


class OzonAdapter(MarketplaceAdapter):
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

    @async_retry(max_retries=3, base_delay=2.0, max_delay=60.0)
    async def _post(self, endpoint: str, data: Optional[Dict] = None) -> Any:
        url = f"{self.BASE_URL}{endpoint}"
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=self.headers, json=data or {}, timeout=30.0)
            response.raise_for_status()
            return response.json()

    async def _post_with_delay(self, endpoint: str, data: Optional[Dict] = None) -> Any:
        await asyncio.sleep(0.6)
        return await self._post(endpoint, data)

    def _parse_date(self, date_str: str, fallback: datetime) -> datetime:
        if not date_str:
            return fallback
        try:
            return datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return fallback

    async def get_sales(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        data = await self._post_with_delay(
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
                "date": datetime.strptime(dimensions.get("day", ""), "%Y-%m-%d") if dimensions.get("day") else date_from,
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
        orders = []

        fbo_data = await self._post_with_delay(
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
            created_at = self._parse_date(item.get("created_at", ""), date_from)
            for product in item.get("products", []):
                orders.append({
                    "date": created_at,
                    "external_sku": str(product.get("offer_id", "")),
                    "external_id": str(product.get("sku", "")),
                    "quantity": product.get("quantity", 1),
                    "price": Decimal(str(product.get("price", "0") or "0")),
                    "status": item.get("status", ""),
                })

        fbs_data = await self._post_with_delay(
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
            created_at = self._parse_date(item.get("created_at", ""), date_from)
            for product in item.get("products", []):
                orders.append({
                    "date": created_at,
                    "external_sku": str(product.get("offer_id", "")),
                    "external_id": str(product.get("sku", "")),
                    "quantity": product.get("quantity", 1),
                    "price": Decimal(str(product.get("price", "0") or "0")),
                    "status": item.get("status", ""),
                })

        return orders

    async def get_stocks(self) -> List[Dict[str, Any]]:
        try:
            data = await self._post_with_delay(
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
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning("Ozon /v3/product/info/stocks returned 404, skipping stocks")
                return []
            raise

    async def get_product_info(self, offer_ids: List[str]) -> Dict[str, str]:
        """Fetch product names by offer_id from Ozon API.

        Returns a mapping offer_id -> name.
        """
        if not offer_ids:
            return {}

        try:
            data = await self._post_with_delay(
                "/v3/product/info/list",
                {"offer_id": list(set(offer_ids))},
            )

            result = {}
            for item in data.get("items", []):
                offer_id = str(item.get("offer_id", ""))
                name = item.get("name") or item.get("product_name") or offer_id
                if offer_id:
                    result[offer_id] = name
            return result
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning("Ozon /v3/product/info/list returned 404, names unavailable")
            else:
                logger.warning("Ozon /v3/product/info/list failed: %s", e)
            return {}

    async def get_adverts(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        logger.warning("Ozon ads skipped: requires Performance API (OAuth). Seller API does not provide campaign endpoints.")
        return []

    async def get_prices(self) -> List[Dict[str, Any]]:
        try:
            data = await self._post_with_delay(
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
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning("Ozon /v5/product/info/prices returned 404, skipping prices")
                return []
            raise

    async def get_finance_report(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        try:
            data = await self._post_with_delay(
                "/v1/finance/realization",
                {
                    "date_from": date_from.strftime("%Y-%m-%d"),
                    "date_to": date_to.strftime("%Y-%m-%d"),
                },
            )

            reports = []
            for item in data.get("realization", []):
                reports.append({
                    "date": datetime.strptime(item.get("date", ""), "%Y-%m-%d") if item.get("date") else date_from,
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
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning("Ozon /v1/finance/realization returned 404, skipping finance")
                return []
            raise