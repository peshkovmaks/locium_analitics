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


class RateLimitExceeded(Exception):
    """Raised when WB asks for a retry interval we are unwilling to block for."""


class WildberriesAdapter(MarketplaceAdapter):
    """Adapter for Wildberries API."""

    BASE_URLS = {
        "statistics": "https://statistics-api.wildberries.ru",
        "marketplace": "https://marketplace-api.wildberries.ru",
        "advert": "https://advert-api.wildberries.ru",
        "analytics": "https://seller-analytics-api.wildberries.ru",
        "finance": "https://finance-api.wildberries.ru",
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
        """Make GET request to WB API with retry.

        Respects WB's x-ratelimit-retry / Retry-After headers. If WB asks for
        a wait longer than 5 minutes we fail fast instead of blocking the sync
        task for an hour.
        """
        url = f"{self.BASE_URLS[base]}{endpoint}"
        await self._throttle()
        async with self._http_client() as client:
            response = await client.get(url, headers=self.headers, params=params, timeout=120.0)
            if response.status_code == 429:
                retry_after = response.headers.get("x-ratelimit-retry") or response.headers.get("Retry-After")
                try:
                    retry_seconds = float(retry_after) if retry_after else 60.0
                except (ValueError, TypeError):
                    retry_seconds = 60.0
                # If WB wants us to wait more than 5 minutes, abort the sync
                # with a clear reason rather than retrying in a tight loop.
                if retry_seconds > 300:
                    raise RateLimitExceeded(
                        f"WB rate limit for {endpoint}: retry after {retry_seconds:.0f}s"
                    )
                await asyncio.sleep(retry_seconds)
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

    async def _finance_post(
        self, endpoint: str, data: Optional[Dict] = None
    ) -> Any:
        """Make POST request to WB Finance API with 60s throttle.

        Finance endpoints have a 1 request/min limit; violating it triggers a
        multi-hour penalty, so we serialize all finance calls globally.
        """
        await self._throttle()
        url = f"{self.BASE_URLS['finance']}{endpoint}"
        async with self._http_client() as client:
            response = await client.post(url, headers=self.headers, json=data, timeout=120.0)
            self._touch()
            if response.status_code == 429:
                retry_after = response.headers.get("x-ratelimit-retry") or response.headers.get("Retry-After")
                try:
                    retry_seconds = float(retry_after) if retry_after else 60.0
                except (ValueError, TypeError):
                    retry_seconds = 60.0
                if retry_seconds > 300:
                    raise RateLimitExceeded(
                        f"WB finance rate limit for {endpoint}: retry after {retry_seconds:.0f}s"
                    )
                await asyncio.sleep(retry_seconds)
                response = await client.post(url, headers=self.headers, json=data, timeout=120.0)
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

    async def _advert_get(self, endpoint: str, params: Optional[Dict] = None) -> Any:
        """GET against the Advert API without the statistics _throttle.

        Advert endpoints have their own rate limits (≈3/min); calling them
        behind the 70s statistics throttle would slow the sync down for no
        benefit. Keep a small guard to avoid bursts.
        """
        url = f"{self.BASE_URLS['advert']}{endpoint}"
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
        """Stocks are currently disabled for WB.

        The warehouse-remains report is available but not needed for the current
        dashboard. Keeping it disabled avoids Analytics API rate limits.
        """
        logger.warning("WB stocks skipped for shop %s: not required", self.shop_id)
        return []

    async def _get_advert_campaigns(self) -> List[Dict[str, Any]]:
        """Fetch active WB advert campaigns.

        Uses /adv/v1/promotion/count which lists campaigns grouped by status.
        """
        try:
            data = await self._advert_get("/adv/v1/promotion/count")
        except Exception as e:
            logger.warning("WB advert campaign list failed for shop %s: %s", self.shop_id, e)
            return []

        campaigns = []
        for group in data.get("adverts", []) if isinstance(data, dict) else []:
            for advert in group.get("advert_list", []) or []:
                advert_id = advert.get("advertId")
                if advert_id:
                    campaigns.append({
                        "advert_id": int(advert_id),
                        "status": group.get("status"),
                        "type": group.get("type"),
                    })
        return campaigns

    async def _get_advert_stats(
        self, campaign_ids: List[int], date_from: datetime, date_to: datetime
    ) -> List[Dict[str, Any]]:
        """Fetch WB advert full stats for the given campaign ids.

        GET /adv/v3/fullstats accepts ids as a comma-separated query parameter.
        Rate limit: 3 req/min; we call once with all ids.
        """
        if not campaign_ids:
            return []

        params = {
            "ids": ",".join(str(i) for i in campaign_ids),
            "beginDate": date_from.strftime("%Y-%m-%d"),
            "endDate": date_to.strftime("%Y-%m-%d"),
        }
        try:
            data = await self._advert_get("/adv/v3/fullstats", params=params)
        except Exception as e:
            logger.warning("WB advert stats failed for shop %s: %s", self.shop_id, e)
            return []

        rows = data if isinstance(data, list) else []
        return rows

    async def get_adverts(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Fetch WB advertising stats via Advert API v3/fullstats.

        Campaigns are listed via /adv/v1/promotion/count and then stats are
        fetched in bulk. Spend is rolled up by day; nmId is used as
        external_sku when available so it can be matched to sales later.
        """
        campaigns = await self._get_advert_campaigns()
        if not campaigns:
            logger.info("WB shop %s has no advert campaigns", self.shop_id)
            return []

        campaign_ids = [c["advert_id"] for c in campaigns]
        stats = await self._get_advert_stats(campaign_ids, date_from, date_to)
        if not stats:
            return []

        adverts = []
        for campaign in stats:
            advert_id = campaign.get("advertId")
            if not advert_id:
                continue
            for day in campaign.get("days", []) or []:
                day_dt = None
                try:
                    day_dt = datetime.strptime(str(day.get("date", "")).split("T")[0], "%Y-%m-%d")
                except (ValueError, TypeError):
                    day_dt = date_from

                # Top-level daily aggregates (sum across all apps).
                spend = Decimal(str(day.get("sum", 0) or 0))
                views = int(day.get("views", 0) or 0)
                clicks = int(day.get("clicks", 0) or 0)
                orders = int(day.get("orders", 0) or 0)
                ctr = Decimal(str(day.get("ctr", 0) or 0))
                cpc = Decimal(str(day.get("cpc", 0) or 0))
                cr = Decimal(str(day.get("cr", 0) or 0))

                # Roll up spend per nmId so external_sku is meaningful.
                nm_spend: Dict[int, Decimal] = {}
                nm_views: Dict[int, int] = {}
                nm_clicks: Dict[int, int] = {}
                nm_orders: Dict[int, int] = {}
                for app in day.get("apps", []) or []:
                    for nm in app.get("nms", []) or []:
                        nm_id = nm.get("nmId")
                        if nm_id is None:
                            continue
                        nm_spend[nm_id] = nm_spend.get(nm_id, Decimal("0")) + Decimal(str(nm.get("sum", 0) or 0))
                        nm_views[nm_id] = nm_views.get(nm_id, 0) + int(nm.get("views", 0) or 0)
                        nm_clicks[nm_id] = nm_clicks.get(nm_id, 0) + int(nm.get("clicks", 0) or 0)
                        nm_orders[nm_id] = nm_orders.get(nm_id, 0) + int(nm.get("orders", 0) or 0)

                if nm_spend:
                    for nm_id, nm_total in nm_spend.items():
                        adverts.append({
                            "date": day_dt,
                            "campaign_id": str(advert_id),
                            "external_sku": str(nm_id),
                            "views": nm_views.get(nm_id, 0),
                            "clicks": nm_clicks.get(nm_id, 0),
                            "ctr": ctr if ctr else Decimal("0"),
                            "cpc": cpc if cpc else Decimal("0"),
                            "spend": nm_total,
                            "orders": nm_orders.get(nm_id, 0),
                            "cr": cr if cr else Decimal("0"),
                        })
                else:
                    # Campaign-level row when no nm breakdown is available.
                    adverts.append({
                        "date": day_dt,
                        "campaign_id": str(advert_id),
                        "external_sku": str(advert_id),
                        "views": views,
                        "clicks": clicks,
                        "ctr": ctr if ctr else Decimal("0"),
                        "cpc": cpc if cpc else Decimal("0"),
                        "spend": spend,
                        "orders": orders,
                        "cr": cr if cr else Decimal("0"),
                    })

        return adverts

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

    def _normalize_wb_srid(self, srid: Any) -> str:
        """Normalize WB order id so finance report rows can match sales rows.

        The statistics API returns srids like "ej.<hex>.0.0". The finance API
        returns the bare hex part (or the same value). Keep only the last
        dot-separated segment when it looks like a position suffix, and strip
        any leading prefix ending with a dot.
        """
        if not srid:
            return ""
        value = str(srid).strip()
        # Strip trailing .X.Y position suffixes.
        while True:
            parts = value.rsplit(".", 1)
            if len(parts) == 2 and parts[1].isdigit():
                value = parts[0]
            else:
                break
        # If the remaining value has a prefix ending with a dot before a hex
        # looking segment, keep the hex segment. Examples:
        #   ej.i417f68ba86af0fd75a9e865c8622d699 -> i417f68ba86af0fd75a9e865c8622d699
        parts = value.split(".")
        if len(parts) > 1:
            return parts[-1]
        return value

    def _finance_value(self, item: Dict[str, Any], *keys: str) -> Any:
        """Get a finance report value trying camelCase and snake_case keys."""
        for key in keys:
            val = item.get(key)
            if val is not None and val != "":
                return val
            # Fallback to snake_case equivalent.
            snake_key = "".join(
                [c if c.islower() else "_" + c.lower() for c in key]
            ).lstrip("_")
            val = item.get(snake_key)
            if val is not None and val != "":
                return val
        return None

    async def get_finance_report(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Get detailed finance report from the new WB Finance API.

        The old /api/v5/supplier/reportDetailByPeriod was disabled in July 2026.
        The replacement is POST /api/finance/v1/sales-reports/detailed on
        finance-api.wildberries.ru, paginated by rrdId (204 means end of data).
        """
        date_from_str = date_from.strftime("%Y-%m-%d")
        date_to_str = date_to.strftime("%Y-%m-%d")

        all_rows: List[Dict[str, Any]] = []
        last_rrd_id = 0
        while True:
            data = await self._finance_post(
                "/api/finance/v1/sales-reports/detailed",
                {
                    "dateFrom": date_from_str,
                    "dateTo": date_to_str,
                    "period": "daily",
                    "limit": 100000,
                    "rrdId": last_rrd_id,
                },
            )
            rows = data if isinstance(data, list) else []
            if not rows:
                break

            all_rows.extend(rows)
            last_rrd_id = rows[-1].get("rrdId", 0)
            if len(rows) < 100000:
                break

        if all_rows:
            # Log a sample for diagnostics — WB finance field names change often.
            sample = all_rows[0]
            logger.info(
                "WB finance report sample for shop %s: keys=%s external_id=%s external_sku=%s",
                self.shop_id,
                sorted(sample.keys()),
                self._normalize_wb_srid(sample.get("srid")),
                sample.get("vendorCode") or sample.get("sa_name") or "",
            )

        reports = []
        for item in all_rows:
            doc_type = (self._finance_value(item, "docTypeName") or "").lower()
            is_return = "возврат" in doc_type or "return" in doc_type
            quantity = self._finance_value(item, "quantity") or 0
            if is_return and quantity > 0:
                quantity = -quantity

            revenue = Decimal(str(self._finance_value(item, "retailAmount") or 0))
            if is_return:
                revenue = -abs(revenue)

            # Acquisition / processing fee maps to acquiring.
            acquiring = Decimal(str(self._finance_value(item, "acquiringFee") or 0))
            if acquiring == 0:
                acquiring = Decimal(str(self._finance_value(item, "acquiring_amount") or 0))

            external_sku = str(
                self._finance_value(item, "vendorCode")
                or self._finance_value(item, "sa_name")
                or ""
            )
            external_id = self._normalize_wb_srid(self._finance_value(item, "srid"))

            reports.append({
                "date": datetime.strptime(self._finance_value(item, "rrDate"), "%Y-%m-%d") if self._finance_value(item, "rrDate") else date_from,
                "external_sku": external_sku,
                "external_id": external_id,
                "quantity": quantity,
                "price": Decimal(str(self._finance_value(item, "retailPrice") or 0)),
                "revenue": revenue,
                "commission": Decimal(str(self._finance_value(item, "ppvzSalesCommission") or 0)),
                "logistics": Decimal(str(self._finance_value(item, "deliveryService") or 0)),
                "storage": Decimal(str(self._finance_value(item, "paidStorage") or 0)),
                "returns": Decimal(str(self._finance_value(item, "returnAmount") or 0)),
                "insurance": Decimal("0"),
                "acquiring": acquiring,
                "other": Decimal(str(self._finance_value(item, "deduction") or 0)) + Decimal(str(self._finance_value(item, "penalty") or 0)),
            })
        return reports
