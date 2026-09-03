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

import asyncio
import csv
import httpx
import io
import logging
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional
from decimal import Decimal
from urllib.parse import urlparse

from app.adapters.base import MarketplaceAdapter
from app.utils.retry import async_retry

logger = logging.getLogger(__name__)


class YandexMarketAdapter(MarketplaceAdapter):
    """Adapter for Yandex Market Partner API."""

    BASE_URL = "https://api.partner.market.yandex.ru"

    def __init__(self, shop_id: str, credentials: Dict[str, Any]):
        super().__init__(shop_id, credentials)
        self.api_key = str(credentials.get("api_key", "")).strip()
        self.oauth_token = str(credentials.get("oauth_token", "")).strip()
        self.business_id = str(credentials.get("business_id", "")).strip()
        self.campaign_id = str(credentials.get("campaign_id", "")).strip()

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
        """Check credentials by calling a lightweight orders stats endpoint.

        GET /v2/campaigns is not always available with Api-Key auth; the stats
        endpoint we actually use is a more reliable check.
        """
        if not self.campaign_id:
            return False
        try:
            await self._post(
                f"/v2/campaigns/{self.campaign_id}/stats/orders",
                {"dateFrom": "2026-01-01", "dateTo": "2026-01-01", "limit": 1},
            )
            return True
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

    async def _get_with_delay(self, endpoint: str, params: Optional[Dict] = None) -> Any:
        """Make GET request to YM API with a small delay to avoid rate limits."""
        await asyncio.sleep(0.6)
        return await self._get(endpoint, params)

    async def _fetch_orders(
        self,
        date_from: datetime,
        date_to: datetime,
    ) -> List[Dict[str, Any]]:
        """Fetch all orders from Yandex Market.

        Uses GET /v2/campaigns/{campaignId}/orders. The endpoint has a hard
        limit of 30 days per request and returns at most 50 orders per page,
        so we split the range into 30-day chunks and paginate within each chunk.
        """
        all_orders: List[Dict[str, Any]] = []

        chunk_start = date_from
        while chunk_start < date_to:
            chunk_end = min(chunk_start + timedelta(days=30), date_to)
            page_token = None
            while True:
                params: Dict[str, Any] = {
                    "fromDate": chunk_start.strftime("%Y-%m-%d"),
                    "toDate": chunk_end.strftime("%Y-%m-%d"),
                    "limit": 50,
                }
                if page_token:
                    params["pageToken"] = page_token

                data = await self._get_with_delay(
                    f"/v2/campaigns/{self.campaign_id}/orders",
                    params,
                )
                orders = data if isinstance(data, list) else data.get("orders", [])
                all_orders.extend(orders)

                paging = data.get("paging") if isinstance(data, dict) else None
                page_token = paging.get("nextPageToken") if paging else None
                if not page_token:
                    break

            chunk_start = chunk_end

        return all_orders

    @staticmethod
    def _parse_ym_datetime(value: str) -> datetime:
        """Parse Yandex Market datetime strings like '30-08-2026 19:22:59'."""
        value = value or ""
        for fmt in ("%d-%m-%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d"):
            try:
                return datetime.strptime(value, fmt)
            except ValueError:
                continue
        return datetime.utcnow()

    async def get_sales(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Get orders from Yandex Market.

        Uses GET /v2/campaigns/{campaignId}/orders, which returns full order
        details including buyer price, seller price, subsidies and discounts.
        """
        orders = await self._fetch_orders(date_from, date_to)

        sales = []
        for order in orders:
            order_status = (order.get("status") or "").upper()
            substatus = (order.get("substatus") or "").upper()
            order_date = self._parse_ym_datetime(order.get("creationDate", ""))
            # `id` is deprecated since 2026-10-05; prefer `orderId` when available.
            order_id = str(order.get("orderId") or order.get("id", ""))

            is_return = order_status in (
                "CANCELLED",
                "CANCELLED_BY_CUSTOMER",
                "RETURNED",
                "PARTIALLY_RETURNED",
            ) or substatus in ("CANCELLED", "RETURNED")

            for product in order.get("items", []):
                shop_sku = str(product.get("offerId") or product.get("shopSku") or "")
                quantity = int(product.get("count", 1) or 1)

                # buyerPrice = what the buyer paid after discounts. This is the
                #   actual per-item price that determines seller revenue before
                #   subsidies. The deprecated `price` field is avoided.
                # buyerPriceBeforeDiscount / priceBeforeDiscount are list prices,
                #   used only as a reference; the dashboard "Revenue" line matches
                #   buyer paid + discount compensation (i.e. actual revenue).
                buyer_price = Decimal(str(product.get("buyerPrice", 0) or 0))
                # Fallback to deprecated `price` only when buyerPrice is missing.
                seller_price = buyer_price
                if seller_price <= 0:
                    seller_price = Decimal(str(product.get("price", 0) or 0))

                # Subsidies are discount-compensation accruals from Yandex Market.
                # They are added to buyer paid to get the seller's actual revenue.
                # Order-level subsidies already contain the sum of item-level
                # compensations plus order-wide ones (e.g. delivery discounts), so
                # we distribute the order-level total by the item's share of the
                # order value to avoid double counting.
                order_subsidies = order.get("subsidies") or []
                order_subsidy_total = sum(
                    Decimal(str(s.get("amount", 0) or 0)) for s in order_subsidies
                )
                order_items_total = sum(
                    Decimal(str(i.get("buyerPrice", 0) or 0)) * int(i.get("count", 1) or 1)
                    for i in order.get("items", [])
                ) or Decimal("1")
                item_total = buyer_price * quantity
                share = item_total / order_items_total if order_items_total > 0 else Decimal("0")
                marketplace_discount = order_subsidy_total * share

                sales.append({
                    "date": order_date,
                    "external_sku": shop_sku,
                    "external_id": order_id,
                    "name": product.get("offerName") or product.get("name") or shop_sku,
                    "quantity": quantity,
                    "price": seller_price,
                    "customer_price": buyer_price,
                    "marketplace_discount": marketplace_discount,
                    "revenue": seller_price * quantity,
                    "commission": Decimal("0"),
                    "logistics": Decimal("0"),
                    "storage": Decimal("0"),
                    "advertising": Decimal("0"),
                    "returns": Decimal("0"),
                    "other": Decimal("0"),
                    "is_return": is_return,
                })
        return sales

    async def get_orders(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Get orders from Yandex Market.

        Same data as get_sales.
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

    async def get_balance(self) -> Optional[Dict[str, Any]]:
        """Yandex Market Partner API does not expose current seller balance.

        Payment reports are historical and async; there is no endpoint that
        returns the current account balance or the next payout date.
        We return a marker so the dashboard can show "not supported".
        """
        logger.warning(
            "Yandex Market does not expose current balance via API for shop %s", self.shop_id
        )
        return {"is_supported": False, "currency": "RUB"}

    async def get_finance_report(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Get finance report from Yandex Market.

        Uses /v2/reports/united-marketplace-services/generate.
        We request the report by accrual date range (dateFrom/dateTo). This
        endpoint returns an XLSX report with detailed expenses per SKU/service,
        which we then parse and categorise into commission, logistics,
        advertising, acquiring, etc.

        The endpoint rejects ranges longer than ~30 days and has a strict rate
        limit (about 1 request per 2 minutes), so we split the requested range
        into 28-day chunks and sleep between them.
        """
        if not self.business_id:
            logger.warning("YM Business ID not available, skipping finance report")
            return []

        chunk_days = 28
        aggregated: Dict[str, Dict[str, Decimal]] = {}
        chunk_start = date_from
        chunk_index = 0

        while chunk_start < date_to:
            chunk_end = min(chunk_start + timedelta(days=chunk_days), date_to)
            chunk_index += 1
            logger.info(
                "YM finance report chunk %s: %s -> %s",
                chunk_index,
                chunk_start.date(),
                chunk_end.date(),
            )

            reports = await self._generate_finance_report_chunk(chunk_start, chunk_end)
            for row in reports:
                sku = row.get("external_sku", "")
                if sku not in aggregated:
                    aggregated[sku] = {
                        "commission": Decimal("0"),
                        "logistics": Decimal("0"),
                        "storage": Decimal("0"),
                        "advertising": Decimal("0"),
                        "returns": Decimal("0"),
                        "insurance": Decimal("0"),
                        "acquiring": Decimal("0"),
                        "other": Decimal("0"),
                    }
                for key in aggregated[sku]:
                    aggregated[sku][key] += Decimal(str(row.get(key, 0) or 0))

            chunk_start = chunk_end
            if chunk_start < date_to:
                # Yandex Market enforces ~1 report per 2.5 minutes for this resource.
                await asyncio.sleep(150)

        result = []
        for sku, amounts in aggregated.items():
            result.append({
                "date": datetime.utcnow(),
                "external_sku": sku,
                "external_id": "",
                "quantity": 0,
                "price": Decimal("0"),
                "revenue": Decimal("0"),
                **amounts,
            })

        logger.info("YM finance report parsed: %s SKU expense records", len(result))
        return result

    async def get_key_indicators_report(
        self,
        date_from: datetime,
        date_to: datetime,
        detalization: str = "MONTH",
    ) -> List[Dict[str, Any]]:
        """Get key indicators report from Yandex Market.

        This report mirrors the "Ключевые показатели" page in the seller's
        cabinet. It returns period-level totals for revenue, orders, average
        check, marketplace services and promotion costs. We parse the full
        sheet and return one record per period so SyncService can distribute
        expenses across sales in that period.
        """
        if not self.business_id:
            logger.warning("YM Business ID not available, skipping key indicators report")
            return []

        payload = {
            "businessId": int(self.business_id) if self.business_id.isdigit() else 0,
            "detalizationLevel": detalization,
        }

        gen_response = await self._post_finance_report(
            "/v2/reports/key-indicators/generate",
            payload,
        )
        report_id = gen_response.get("result", {}).get("reportId") or gen_response.get("reportId")
        if not report_id:
            logger.warning("YM key indicators report did not return reportId")
            return []

        file_url = None
        for attempt in range(30):
            await asyncio.sleep(5)
            try:
                info = await self._get(f"/v2/reports/info/{report_id}")
            except httpx.HTTPStatusError as e:
                logger.warning("YM report info request failed: %s", e)
                continue

            status = (info.get("result") or info or {}).get("status", "").upper()
            if status == "DONE":
                file_url = (info.get("result") or info or {}).get("file")
                logger.info("YM key indicators report ready after %s attempts", attempt + 1)
                break
            elif status in ("FAILED", "ERROR", "CANCELLED"):
                logger.warning("YM key indicators report generation failed with status: %s", status)
                return []

        if not file_url:
            logger.warning("YM key indicators report did not become ready in time")
            return []

        content = await self._download_report_file(file_url)
        if not content:
            return []
        return self._parse_key_indicators_xlsx(content, date_from, date_to)

    async def _download_report_file(self, file_url: str) -> Optional[bytes]:
        """Download a report file from Yandex Market."""
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
                response = await client.get(file_url)
                response.raise_for_status()
                return response.content
        except Exception as e:
            logger.warning("YM report download failed: %s", e)
            return None

    def _parse_key_indicators_xlsx(
        self,
        content: bytes,
        date_from: datetime,
        date_to: datetime,
    ) -> List[Dict[str, Any]]:
        """Parse YM key indicators XLSX and return period-level rows."""
        if not content.startswith(b"PK"):
            logger.warning("YM key indicators report is not an XLSX/ZIP archive")
            return []

        # Russian month names -> number
        _MONTHS = {
            "январь": 1, "февраль": 2, "март": 3, "апрель": 4,
            "май": 5, "июнь": 6, "июль": 7, "август": 8,
            "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
        }

        def _parse_period(value: Any) -> Optional[Dict[str, Any]]:
            text = str(value).strip()
            lowered = text.lower()
            # Skip total/aggregation rows like "Итого с 01.07.2026".
            if "итого" in lowered:
                return None
            # Formats: "Август 2026", "август 26", "2026-08", "08.2026"
            for name, num in _MONTHS.items():
                if name in lowered:
                    year_part = lowered.replace(name, "").strip()
                    if not year_part:
                        return None
                    year = int(year_part) if len(year_part) == 4 else (2000 + int(year_part))
                    month = num
                    start = datetime(year, month, 1)
                    if month == 12:
                        end = datetime(year + 1, 1, 1) - timedelta(seconds=1)
                    else:
                        end = datetime(year, month + 1, 1) - timedelta(seconds=1)
                    return {"start": start, "end": end}
            # Try ISO "2026-08" or dotted "08.2026"
            for fmt in ("%Y-%m", "%m.%Y"):
                try:
                    parsed = datetime.strptime(lowered, fmt)
                    start = parsed.replace(day=1)
                    if start.month == 12:
                        end = datetime(start.year + 1, 1, 1) - timedelta(seconds=1)
                    else:
                        end = datetime(start.year, start.month + 1, 1) - timedelta(seconds=1)
                    return {"start": start, "end": end}
                except ValueError:
                    continue
            return None

        def _num(value: Any) -> Decimal:
            if value is None or value == "":
                return Decimal("0")
            text = str(value).replace(" ", "").replace(",", ".")
            try:
                return Decimal(text)
            except Exception:
                return Decimal("0")

        reports: List[Dict[str, Any]] = []
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                sheet_names = self._read_sheet_names(z)
                target_file = None
                for file_name, name in sheet_names.items():
                    lowered_name = name.lower()
                    if "все" in lowered_name or "full" in lowered_name or "key_indicators_full" in file_name.lower():
                        target_file = file_name
                        break
                if not target_file:
                    # Fallback to the "Расходы" (expenses) sheet.
                    for file_name, name in sheet_names.items():
                        if "расходы" in name.lower():
                            target_file = file_name
                            break
                if not target_file:
                    logger.warning("Could not find key indicators full/expenses sheet in YM report")
                    return []

                rows = self._parse_xlsx_sheet(z, target_file)
                if not rows:
                    return []

                header_row_index = None
                header_map: Dict[str, int] = {}
                for idx, row in enumerate(rows):
                    cells = [str(c).strip().lower() for c in row]
                    if "период" in cells:
                        header_row_index = idx
                        header_map = {c: i for i, c in enumerate(cells) if c}
                        break

                if header_row_index is None:
                    logger.warning("Could not find header row in YM key indicators sheet")
                    return []

                period_idx = self._find_col(header_map, "период", "period")
                gmv_idx = self._find_col(header_map, "выручка", "gmv")
                orders_idx = self._find_col(header_map, "доставленные заказы", "ordersdelivered")
                avg_price_idx = self._find_col(header_map, "средний чек заказа", "ordersavgprice")
                subsidy_idx = self._find_col(header_map, "все платежи за скидки", "totalsubsidy")
                services_idx = self._find_col(header_map, "стоимость всех услуг маркета без продвижения", "serviceswithoutpromotion")
                promotion_idx = self._find_col(header_map, "стоимость услуг продвижения", "promotionservices")
                fee_idx = self._find_col(header_map, "стоимость размещения товаров на витрине", "fee")
                acquiring_idx = self._find_col(header_map, "приём и перевод платежа покупателя", "paymentacceptanceandtransfer")
                logistics_idx = self._find_col(header_map, "стоимость услуг логистики", "logisticservices")
                warehouse_idx = self._find_col(header_map, "стоимость услуг склада", "warehouseservices")
                boost_idx = self._find_col(header_map, "расходы на буст продаж", "boost")
                promotion_shows_idx = self._find_col(header_map, "расходы на продвижение с оплатой за показы", "promotionwithshows")
                loyalty_idx = self._find_col(header_map, "участие в программе лояльности и отзывы", "loyaltyparticipationfee")
                extended_idx = self._find_col(header_map, "расширенный доступ к сервисам маркетплейса", "extendedaccesspayment")

                for row in rows[header_row_index + 1:]:
                    if period_idx is None or period_idx >= len(row):
                        continue
                    period = _parse_period(row[period_idx])
                    if period is None:
                        continue
                    # Skip rows outside the requested range
                    if period["end"] < date_from.replace(tzinfo=None) or period["start"] > date_to.replace(tzinfo=None):
                        continue

                    gmv = _num(row[gmv_idx] if gmv_idx is not None and gmv_idx < len(row) else "")
                    orders = int(_num(row[orders_idx] if orders_idx is not None and orders_idx < len(row) else ""))
                    avg_price = _num(row[avg_price_idx] if avg_price_idx is not None and avg_price_idx < len(row) else "")
                    subsidy = _num(row[subsidy_idx] if subsidy_idx is not None and subsidy_idx < len(row) else "")
                    services_without_promotion = _num(row[services_idx] if services_idx is not None and services_idx < len(row) else "")
                    promotion_services = _num(row[promotion_idx] if promotion_idx is not None and promotion_idx < len(row) else "")
                    fee = _num(row[fee_idx] if fee_idx is not None and fee_idx < len(row) else "")
                    acquiring = _num(row[acquiring_idx] if acquiring_idx is not None and acquiring_idx < len(row) else "")
                    logistics = _num(row[logistics_idx] if logistics_idx is not None and logistics_idx < len(row) else "")
                    warehouse = _num(row[warehouse_idx] if warehouse_idx is not None and warehouse_idx < len(row) else "")
                    boost = _num(row[boost_idx] if boost_idx is not None and boost_idx < len(row) else "")
                    promotion_with_shows = _num(row[promotion_shows_idx] if promotion_shows_idx is not None and promotion_shows_idx < len(row) else "")
                    loyalty = _num(row[loyalty_idx] if loyalty_idx is not None and loyalty_idx < len(row) else "")
                    extended = _num(row[extended_idx] if extended_idx is not None and extended_idx < len(row) else "")

                    reports.append({
                        "date_from": period["start"],
                        "date_to": period["end"],
                        "gmv": gmv,
                        "orders_delivered": orders,
                        "avg_price": avg_price,
                        "total_subsidy": subsidy,
                        "services_without_promotion": services_without_promotion,
                        "promotion_services": promotion_services,
                        # Category breakdown
                        "commission": fee,
                        "logistics": logistics,
                        "storage": warehouse,
                        "advertising": promotion_services + boost + promotion_with_shows,
                        "acquiring": acquiring,
                        "other": loyalty + extended,
                        "returns": Decimal("0"),
                        "insurance": Decimal("0"),
                    })
        except Exception as e:
            logger.warning("YM key indicators XLSX parsing failed: %s", e)
            return []

        logger.info("YM key indicators report parsed: %s period records", len(reports))
        return reports

    async def _post_finance_report(
        self,
        endpoint: str,
        data: Optional[Dict] = None,
    ) -> Any:
        """POST to YM finance report endpoints with a relaxed rate-limit retry.

        The /v2/reports/... endpoints return 420 when the caller exceeds the
        report-generation rate limit. Experience shows the cooldown is around
        2 minutes, so we sleep 150s between attempts instead of the fast
        exponential backoff used for normal API calls.
        """
        url = f"{self.BASE_URL}{endpoint}"
        last_exception = None
        for attempt in range(5):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(url, headers=self.headers, json=data or {}, timeout=120.0)
                    if response.status_code == 429 or response.status_code == 420:
                        response.raise_for_status()
                    response.raise_for_status()
                    return response.json()
            except (httpx.ConnectTimeout, httpx.ConnectError) as e:
                last_exception = e
                logger.warning(
                    "YM finance report connection failed (attempt %s/5), retrying in 30s: %s",
                    attempt + 1,
                    e,
                )
                await asyncio.sleep(30)
                continue
            except httpx.HTTPStatusError as e:
                last_exception = e
                if e.response.status_code in (429, 420):
                    logger.warning(
                        "YM finance report rate limited (attempt %s/5), sleeping 150s...",
                        attempt + 1,
                    )
                    await asyncio.sleep(150)
                    continue
                raise
        raise last_exception or RuntimeError("YM finance report POST failed after retries")

    async def _generate_finance_report_chunk(
        self,
        date_from: datetime,
        date_to: datetime,
    ) -> List[Dict[str, Any]]:
        """Generate and download one finance report chunk.

        Accrual-date range is the format that currently works and matches the
        date filtering in _download_and_parse_ym_report. Year/month is kept as
        a fallback in case Yandex Market changes the accepted payload shape.
        """
        payloads = [
            {
                "businessId": int(self.business_id) if self.business_id.isdigit() else 0,
                "dateFrom": date_from.strftime("%Y-%m-%d"),
                "dateTo": date_to.strftime("%Y-%m-%d"),
            },
            {
                "businessId": int(self.business_id) if self.business_id.isdigit() else 0,
                "year": date_to.year,
                "month": date_to.month,
            },
        ]

        gen_response = None
        for payload in payloads:
            try:
                gen_response = await self._post_finance_report(
                    "/v2/reports/united-marketplace-services/generate",
                    payload,
                )
                break
            except httpx.HTTPStatusError as e:
                logger.warning("YM finance report generation attempt failed: %s", e)
                await asyncio.sleep(2)
                continue

        if gen_response is None:
            logger.warning("YM finance report generation failed for all payload variants")
            return []

        report_id = gen_response.get("result", {}).get("reportId") or gen_response.get("reportId")
        if not report_id:
            logger.warning("YM finance report did not return reportId")
            return []

        logger.info("YM finance report generation started: %s", report_id)

        # Poll for report completion
        file_url = None
        for attempt in range(30):
            await asyncio.sleep(5)
            try:
                info = await self._get(f"/v2/reports/info/{report_id}")
            except httpx.HTTPStatusError as e:
                logger.warning("YM report info request failed: %s", e)
                continue

            status = (info.get("result") or info or {}).get("status", "").upper()
            if status == "DONE":
                file_url = (info.get("result") or info or {}).get("file")
                logger.info("YM finance report ready after %s attempts", attempt + 1)
                break
            elif status in ("FAILED", "ERROR", "CANCELLED"):
                logger.warning("YM finance report generation failed with status: %s", status)
                return []

        if not file_url:
            logger.warning("YM finance report did not become ready in time")
            return []

        return await self._download_and_parse_ym_report(file_url, date_from, date_to)

    async def _download_and_parse_ym_report(
        self,
        file_url: str,
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Download YM unified marketplace services report and aggregate expenses by SKU.

        Yandex Market returns the report as an XLSX file (a ZIP archive containing
        several XML worksheets). We parse the XML directly to avoid an extra
        dependency on openpyxl/pandas.
        """
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=60.0) as client:
                response = await client.get(file_url)
                response.raise_for_status()
                content = response.content
        except Exception as e:
            logger.warning("YM report download failed: %s", e)
            return []

        if not content.startswith(b"PK"):
            logger.warning("YM report is not an XLSX/ZIP archive; cannot parse")
            return []

        aggregated: Dict[str, Dict[str, Decimal]] = {}
        sheet_categories = {
            "размещение товара": "commission",
            "размещение товаров и услуг": "commission",
            "order for sale": "commission",
            "warehouse processing": "logistics",
            "acceptance of supply": "logistics",
            "буст продаж": "advertising",
            "sales boost": "advertising",
            "installment plan": "advertising",
            "shelves": "advertising",
            "boost sales with pay-per-views": "advertising",
            "product banners": "advertising",
            "banners": "advertising",
            "push-notifications": "advertising",
            "pop-up notifications": "advertising",
            "доставка покупателю": "logistics",
            "delivery to buyer": "logistics",
            "доставка (средняя миля)": "logistics",
            "delivery (middle mile)": "logistics",
            "express delivery": "logistics",
            "delivery from abroad": "logistics",
            "страхование": "insurance",
            "insurance": "insurance",
            "эквайринг": "acquiring",
            "acquiring": "acquiring",
            "приём платежа": "acquiring",
            "payment acceptance": "acquiring",
            "перевод платежа": "acquiring",
            "payment transfer": "acquiring",
            "order for payment transfer": "acquiring",
            "loyalty program and reviews": "other",
            "подписки": "other",
            "subscriptions": "other",
        }

        def _sheet_category(sheet_name: str) -> Optional[str]:
            lowered = sheet_name.lower()
            for key, cat in sheet_categories.items():
                if key in lowered:
                    return cat
            return None

        def _parse_datetime(value: Any) -> Optional[datetime]:
            if not value:
                return None
            text = str(value).strip()
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
                try:
                    return datetime.strptime(text[:len(fmt)], fmt)
                except ValueError:
                    continue
            return None

        def _in_range(value: Any) -> bool:
            if date_from is None or date_to is None:
                return True
            parsed = _parse_datetime(value)
            if parsed is None:
                return True
            # Sale.date is naive UTC; compare against naive bounds.
            df = date_from.replace(tzinfo=None) if date_from.tzinfo else date_from
            dt = date_to.replace(tzinfo=None) if date_to.tzinfo else date_to
            return df <= parsed <= dt

        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                sheet_names = self._read_sheet_names(z)
                for sheet_file in z.namelist():
                    if not sheet_file.startswith("xl/worksheets/sheet") or not sheet_file.endswith(".xml"):
                        continue

                    rows = self._parse_xlsx_sheet(z, sheet_file)
                    if not rows:
                        continue

                    sheet_id = sheet_file.replace("xl/worksheets/sheet", "").replace(".xml", "")
                    sheet_name = sheet_names.get(sheet_id, "")
                    default_category = _sheet_category(sheet_name)

                    # Find header row
                    header_row_index = None
                    header_map: Dict[str, int] = {}
                    for idx, row in enumerate(rows):
                        cells = [str(c).strip().lower() for c in row]
                        if "ваш sku" in cells or "ваш sku" in " ".join(cells):
                            header_row_index = idx
                            header_map = {c: i for i, c in enumerate(cells) if c}
                            break

                    # Some sheets (e.g. Subscriptions) have no SKU column.
                    # Aggregate those expenses under an empty SKU so they are
                    # distributed to all sales later.
                    has_sku = header_row_index is not None and self._find_col(header_map, "ваш sku") is not None
                    if has_sku:
                        sku_idx = self._find_col(header_map, "ваш sku")
                    else:
                        sku_idx = None

                    amount_idx = self._find_col(header_map, "стоимость услуги") if header_map else None
                    if amount_idx is None:
                        # Fallback: look for a final service-cost column on sheets
                        # without the standard header (should not normally happen).
                        continue

                    service_idx = self._find_col(header_map, "услуга") if header_map else None
                    date_idx = self._find_col(header_map, "дата и время оказания услуги") if header_map else None
                    if date_idx is None and header_map:
                        date_idx = self._find_col(header_map, "дата оказания услуги")

                    # If we could not find a header row at all but the sheet has a
                    # known category, skip it — we cannot safely aggregate.
                    if not header_row_index and default_category is None:
                        continue

                    for row in rows[(header_row_index or 0) + 1:]:
                        if sku_idx is not None:
                            if sku_idx >= len(row):
                                continue
                            sku = str(row[sku_idx]).strip()
                        else:
                            sku = ""

                        if sku_idx is not None and not sku:
                            continue

                        if date_idx is not None and date_idx < len(row):
                            if not _in_range(row[date_idx]):
                                continue

                        try:
                            amount = Decimal(str(row[amount_idx] if amount_idx < len(row) else "0").replace(",", "."))
                        except Exception:
                            continue

                        category = default_category
                        if service_idx is not None and service_idx < len(row):
                            service_name = str(row[service_idx]).lower()
                            category = self._categorize_ym_service(service_name, sheet_categories) or default_category

                        if not category:
                            continue

                        if sku not in aggregated:
                            aggregated[sku] = {
                                "commission": Decimal("0"),
                                "logistics": Decimal("0"),
                                "storage": Decimal("0"),
                                "advertising": Decimal("0"),
                                "returns": Decimal("0"),
                                "insurance": Decimal("0"),
                                "acquiring": Decimal("0"),
                                "other": Decimal("0"),
                            }
                        aggregated[sku][category] += amount
        except Exception as e:
            logger.warning("YM XLSX parsing failed: %s", e)
            return []

        reports = []
        for sku, amounts in aggregated.items():
            reports.append({
                "date": datetime.utcnow(),
                "external_sku": sku,
                "external_id": "",
                "quantity": 0,
                "price": Decimal("0"),
                "revenue": Decimal("0"),
                **amounts,
            })

        logger.info("YM finance report parsed: %s SKU expense records", len(reports))
        return reports

    def _read_sheet_names(self, z: zipfile.ZipFile) -> Dict[str, str]:
        """Map sheet file names to human-readable sheet names from workbook.xml."""
        names: Dict[str, str] = {}
        try:
            xml = z.read("xl/workbook.xml").decode("utf-8", errors="replace")
            root = ET.fromstring(xml)
            ns = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            for sheet in root.findall(".//main:sheet", ns):
                name = sheet.get("name", "")
                sheet_id = sheet.get("sheetId", "")
                if sheet_id:
                    names[f"xl/worksheets/sheet{sheet_id}.xml"] = name
        except Exception as e:
            logger.warning("Could not read YM workbook sheet names: %s", e)
        return names

    def _parse_xlsx_sheet(self, z: zipfile.ZipFile, sheet_file: str) -> List[List[Any]]:
        """Parse an XLSX worksheet XML into rows of cell values."""
        try:
            xml = z.read(sheet_file).decode("utf-8", errors="replace")
        except Exception:
            return []

        ns = {"main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        try:
            root = ET.fromstring(xml)
        except ET.ParseError:
            return []

        def col_index(col: str) -> int:
            idx = 0
            for ch in col:
                idx = idx * 26 + (ord(ch) - ord("A") + 1)
            return idx - 1

        rows: Dict[int, Dict[int, Any]] = {}
        for row in root.findall(".//main:row", ns):
            row_num = int(row.get("r", 0))
            cells: Dict[int, Any] = {}
            for c in row.findall("main:c", ns):
                ref = c.get("r", "")
                match = re.match(r"([A-Z]+)", ref)
                if not match:
                    continue
                col = match.group(1)
                idx = col_index(col)
                cell_type = c.get("t", "")
                value = ""

                inline = c.find("main:is/main:t", ns)
                if inline is not None:
                    value = inline.text or ""
                else:
                    v = c.find("main:v", ns)
                    if v is not None:
                        text = v.text or ""
                        if cell_type == "n":
                            try:
                                value = float(text)
                            except ValueError:
                                value = text
                        else:
                            value = text
                cells[idx] = value
            if cells:
                rows[row_num] = cells

        if not rows:
            return []

        max_row = max(rows.keys())
        max_col = max(max(cells.keys()) for cells in rows.values())
        return [
            [rows.get(r, {}).get(c, "") for c in range(max_col + 1)]
            for r in range(1, max_row + 1)
        ]

    def _find_col(self, header_map: Dict[str, int], *names: str) -> Optional[int]:
        """Find column index by one of the Russian header names.

        Prefers exact matches and the final amount column to avoid picking
        intermediate columns like "Стоимость услуги без учёта ограничений
        тарифа" instead of "Стоимость услуги (AX = ...), ₽".
        """
        import re

        def _normalize(text: str) -> str:
            return re.sub(r"\s+", " ", text.replace("\n", " ")).strip()

        normalized_map = {_normalize(h): idx for h, idx in header_map.items()}

        for raw_name in names:
            name = _normalize(raw_name)
            # Exact match first
            for header, idx in normalized_map.items():
                if header.rstrip(",₽") == name:
                    return idx
            # Exact match ignoring currency suffix
            for header, idx in normalized_map.items():
                clean = header.replace(", ₽", "").replace("₽", "").strip()
                if clean == name:
                    return idx
            # Final amount column: contains the name and a currency suffix,
            # and does not describe an intermediate calculation.
            for header, idx in normalized_map.items():
                if name in header and "₽" in header and not any(
                    k in header for k in ("без", "учёта", "ограничений", "мин.", "максимальный")
                ):
                    return idx
            # Fallback to substring
            for header, idx in normalized_map.items():
                if name in header:
                    return idx
        return None

    def _categorize_ym_service(self, service_name: str, mapping: Dict[str, str]) -> Optional[str]:
        """Map a Yandex Market service name to an expense category."""
        lowered = service_name.lower()
        for key, category in mapping.items():
            if key in lowered:
                return category
        return None
