"""Wildberries API Adapter.

API docs: https://dev.wildberries.ru/
Auth: API token in header 'Authorization: '
Base URL: https://statistics-api.wildberries.ru (for stats)
         https://marketplace-api.wildberries.ru (for marketplace)
         https://advert-api.wildberries.ru (for adverts)
         https://seller-analytics-api.wildberries.ru (for analytics reports)

Key endpoints:
- GET /api/v1/supplier/sales — sales data
- GET /api/v1/supplier/orders — orders
- GET /api/v5/supplier/reportDetailByPeriod — finance report
- POST /adv/v1/promotion/adverts — advert campaigns
- POST /adv/v2/fullstats — advert stats

NOTE (2026-08): GET /api/v1/supplier/stocks (statistics API) was disabled
by WB on June 23, 2026. Stocks are now fetched via the async "warehouse
remains" report on the Analytics API: create task -> poll status -> download.
The API key used here must have the "Analytics" category enabled, in
addition to "Statistics", or the stocks calls below will fail with 401/403.
Release note: https://dev.wildberries.ru/en/release-notes
"""

import os
import time
import httpx
import logging
import asyncio
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional, ClassVar
from decimal import Decimal

from app.adapters.base import MarketplaceAdapter
from app.utils.retry import async_retry

logger = logging.getLogger(__name__)


class WildberriesAdapter(MarketplaceAdapter):
    """Adapter for Wildberries API."""

    BASE_URLS = {
        "statistics": "https://statistics-api.wildberries.ru",
        "marketplace": "https://marketplace-api.wildberries.ru",
        "advert": "https://advert-api.wildberries.ru",
        "analytics": "https://seller-analytics-api.wildberries.ru",
    }

    # Warehouse-remains report: max 1/min for create+download, 1/5s for status.
    # Far looser than the statistics API, so it must not share _min_interval/
    # _throttle below — those are sized for the 70s statistics limit and would
    # otherwise make each status poll wait a full extra minute.
    _stocks_poll_interval: ClassVar[float] = 10.0
    _stocks_poll_timeout: ClassVar[float] = 600.0  # 10 min ceiling

    # WB statistics API is heavily rate-limited; 70s between requests is safe.
    _min_interval: ClassVar[float] = 70.0
    _global_last_request_at: ClassVar[Optional[float]] = None

    def __init__(self, shop_id: str, credentials: Dict[str, Any]):
        super().__init__(shop_id, credentials)
        self.api_key = credentials.get("api_key", "")
        self.headers = {"Authorization": self.api_key}

    def _http_client(self) -> httpx.AsyncClient:
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        return httpx.AsyncClient(proxy=proxy) if proxy else httpx.AsyncClient()

    @classmethod
    async def _throttle(cls):
        if cls._global_last_request_at is not None:
            elapsed = time.monotonic() - cls._global_last_request_at
            if elapsed < cls._min_interval:
                await asyncio.sleep(cls._min_interval - elapsed)

    @classmethod
    def _touch(cls):
        cls._global_last_request_at = time.monotonic()

    async def authenticate(self) -> bool:
        """Assume the key is valid; actual errors surface during sync."""
        return bool(self.api_key)

    @async_retry(max_retries=3, base_delay=60.0, max_delay=120.0)
    async def _get(self, endpoint: str, base: str = "statistics", params: Optional[Dict] = None) -> Any:
        """Make GET request to WB API with retry."""
        url = f"{self.BASE_URLS[base]}{endpoint}"
        await self._throttle()
        async with self._http_client() as client:
            response = await client.get(url, headers=self.headers, params=params, timeout=120.0)
            self._touch()
            response.raise_for_status()
            return response.json()

    @async_retry(max_retries=3, base_delay=1.0, max_delay=30.0)
    async def _post(self, endpoint: str, base: str = "advert", data: Optional[Dict] = None) -> Any:
        """Make POST request to WB API with retry."""
        url = f"{self.BASE_URLS[base]}{endpoint}"
        async with self._http_client() as client:
            response = await client.post(url, headers=self.headers, json=data, timeout=30.0)
            response.raise_for_status()
            return response.json()

    async def _analytics_get(self, endpoint: str, params: Optional[Dict] = None) -> Any:
        """GET against the Analytics API, without the statistics _throttle.

        The warehouse-remains report has its own (much looser) rate limits,
        so it must not be serialized behind the 70s statistics interval.
        """
        url = f"{self.BASE_URLS['analytics']}{endpoint}"
        async with self._http_client() as client:
            response = await client.get(url, headers=self.headers, params=params, timeout=60.0)
            response.raise_for_status()
            return response.json()

    async def _fetch_statistics(
        self, endpoint: str, date_from: datetime, date_to: datetime
    ) -> List[Dict[str, Any]]:
        """Fetch rows from a WB statistics endpoint using lastChangeDate pagination.

        WB statistics endpoints return at most ~80,000 rows per request and do not
        support a real dateTo. We paginate by passing the lastChangeDate of the
        last row as the next dateFrom.
        """
        items: List[Dict[str, Any]] = []
        last_change_date: Optional[str] = None
        current = date_from

        while True:
            params: Dict[str, Any] = {"dateFrom": current.strftime("%Y-%m-%dT%H:%M:%S"), "flag": 0}
            data = await self._get(endpoint, base="statistics", params=params)
            if not data:
                break

            # Drop the boundary row that we already got on the previous page
            if last_change_date is not None:
                data = [row for row in data if row.get("lastChangeDate") != last_change_date]
            if not data:
                break

            items.extend(data)
            last_change_date = data[-1].get("lastChangeDate")
            try:
                current = datetime.fromisoformat(last_change_date.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                break

            # Fewer than the 80k cap means everything up to this point was fetched
            if len(data) < 80000:
                break

        return items

    async def get_sales(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Get sales data from WB."""
        data = await self._fetch_statistics("/api/v1/supplier/sales", date_from, date_to)

        sales = []
        for item in data:
            is_storno = bool(item.get("IsStorno", False))
            sales.append({
                "date": datetime.fromisoformat(item.get("date", "").replace("Z", "+00:00")),
                "external_sku": item.get("supplierArticle", ""),
                "external_id": str(item.get("srid", "")),
                "quantity": -1 if is_storno else 1,
                "price": Decimal(str(item.get("finishedPrice", 0) or item.get("totalPrice", 0))),
                "revenue": Decimal(str(item.get("forPay", 0) or 0)),
                "commission": Decimal("0"),
                "logistics": Decimal("0"),
                "storage": Decimal("0"),
                "advertising": Decimal("0"),
                "returns": Decimal("0"),
                "other": Decimal("0"),
                "is_return": is_storno,
            })
        return sales

    async def get_orders(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Get orders data."""
        data = await self._fetch_statistics("/api/v1/supplier/orders", date_from, date_to)

        orders = []
        for item in data:
            is_cancel = bool(item.get("isCancel", False))
            orders.append({
                "date": datetime.fromisoformat(item.get("date", "").replace("Z", "+00:00")),
                "external_sku": item.get("supplierArticle", ""),
                "external_id": str(item.get("srid", "")),
                "quantity": 1,
                "price": Decimal(str(item.get("totalPrice", 0) or 0)),
                "status": "cancelled" if is_cancel else "ordered",
            })
        return orders

    async def get_stocks(self) -> List[Dict[str, Any]]:
        """Get current stock levels via the WB warehouse-remains report.

        GET /api/v1/supplier/stocks (statistics API) was disabled by WB on
        June 23, 2026. This is a 3-step async report on the Analytics API:
        1. Create a report task (groupByNm groups rows by WB article).
        2. Poll task status until "done".
        3. Download the finished report.
        """
        task = await self._analytics_get(
            "/api/v1/warehouse_remains",
            params={"groupByNm": "true"},
        )
        task_id = (task or {}).get("data", {}).get("taskId")
        if not task_id:
            raise RuntimeError(f"WB warehouse_remains: no taskId in response: {task}")

        elapsed = 0.0
        status = None
        while elapsed < self._stocks_poll_timeout:
            await asyncio.sleep(self._stocks_poll_interval)
            elapsed += self._stocks_poll_interval
            status_resp = await self._analytics_get(
                f"/api/v1/warehouse_remains/tasks/{task_id}/status"
            )
            status = (status_resp or {}).get("data", {}).get("status")
            if status == "done":
                break
        else:
            raise RuntimeError(
                f"WB warehouse_remains: task {task_id} did not finish "
                f"within {self._stocks_poll_timeout:.0f}s (last status: {status})"
            )

        report = await self._analytics_get(
            f"/api/v1/warehouse_remains/tasks/{task_id}/download"
        )

        stocks = []
        for item in report or []:
            external_id = str(item.get("nmId", ""))
            # The grouped report does not include vendorCode; nmId is the only
            # stable article identifier available here.
            external_sku = external_id
            warehouses = item.get("warehouses") or []

            in_way = sum(
                wh.get("quantity", 0)
                for wh in warehouses
                if wh.get("warehouseName", "").lower().startswith("в пути")
            )

            for wh in warehouses:
                warehouse_name = wh.get("warehouseName", "Unknown")
                # Skip the synthetic totals row; keep per-warehouse rows.
                if "всего" in warehouse_name.lower():
                    continue
                stocks.append({
                    "external_sku": external_sku,
                    "external_id": external_id,
                    "warehouse": warehouse_name,
                    "quantity": wh.get("quantity", 0),
                    "in_way": in_way,
                })

            if not warehouses:
                stocks.append({
                    "external_sku": external_sku,
                    "external_id": external_id,
                    "warehouse": "Unknown",
                    "quantity": 0,
                    "in_way": 0,
                })
        return stocks

    async def get_adverts(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Advertising stats are temporarily disabled for WB.

        WB advert API requires a separate token and endpoint path that differs
        from the statistics token currently stored in credentials. Skipping to
        keep the overall sync healthy.
        """
        logger.warning(
            "WB adverts skipped for shop %s: advert API token/endpoint not available",
            self.shop_id,
        )
        return []

    async def get_prices(self) -> List[Dict[str, Any]]:
        """Current prices are temporarily disabled for WB.

        WB marketplace prices API requires a separate token/endpoint and is not
        needed for the current dashboard. Skipping to keep the overall sync healthy.
        """
        logger.warning(
            "WB prices skipped for shop %s: marketplace API token/endpoint not available",
            self.shop_id,
        )
        return []

    async def get_finance_report(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Get detailed finance report."""
        date_from_str = date_from.strftime("%Y-%m-%d")
        date_to_str = date_to.strftime("%Y-%m-%d")

        data = await self._get(
            "/api/v5/supplier/reportDetailByPeriod",
            params={
                "dateFrom": date_from_str,
                "dateTo": date_to_str,
                "limit": 100000,
            },
        )

        reports = []
        for item in data:
            reports.append({
                "date": datetime.strptime(item.get("rr_dt", ""), "%Y-%m-%d"),
                "external_sku": item.get("sa_name", ""),
                "external_id": str(item.get("nm_id", "")),
                "quantity": item.get("quantity", 0),
                "price": Decimal(str(item.get("retail_price", 0) or 0)),
                "revenue": Decimal(str(item.get("retail_amount", 0) or 0)),
                "commission": Decimal(str(item.get("commission_amount", 0) or 0)),
                "logistics": Decimal(str(item.get("delivery_rub", 0) or 0)),
                "storage": Decimal(str(item.get("storage_fee", 0) or 0)),
                "returns": Decimal(str(item.get("return_amount", 0) or 0)),
                "other": Decimal(str(item.get("deduction", 0) or 0)),
            })
        return reports
