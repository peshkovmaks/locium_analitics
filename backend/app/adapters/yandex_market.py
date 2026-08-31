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
            order_id = str(order.get("id", ""))

            is_return = order_status in (
                "CANCELLED",
                "CANCELLED_BY_CUSTOMER",
                "RETURNED",
                "PARTIALLY_RETURNED",
            ) or substatus in ("CANCELLED", "RETURNED")

            for product in order.get("items", []):
                shop_sku = str(product.get("offerId") or product.get("shopSku") or "")
                quantity = int(product.get("count", 1) or 1)

                # buyerPrice = what the buyer paid after discounts.
                # price = the seller's price before subsidies, which for Yandex
                #   Market equals buyerPrice (subsidies are compensated on top).
                # buyerPriceBeforeDiscount / priceBeforeDiscount = list price,
                #   used only as a reference; the dashboard "Revenue" line matches
                #   buyer paid + discount compensation (i.e. actual revenue).
                buyer_price = Decimal(str(product.get("buyerPrice", 0) or 0))
                seller_price = Decimal(str(product.get("price", 0) or 0))
                if seller_price <= 0:
                    seller_price = buyer_price

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
                # Yandex Market enforces ~1 report per 2 minutes for this resource.
                await asyncio.sleep(120)

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
                gen_response = await self._post(
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
            return date_from <= parsed <= date_to

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
        for name in names:
            # Exact match first
            for header, idx in header_map.items():
                if header.strip().rstrip(",₽") == name:
                    return idx
            # Exact match ignoring currency suffix
            for header, idx in header_map.items():
                clean = header.replace(", ₽", "").replace("₽", "").strip()
                if clean == name:
                    return idx
            # Final amount column: contains the name and a currency suffix,
            # and does not describe an intermediate calculation.
            for header, idx in header_map.items():
                if name in header and "₽" in header and not any(
                    k in header for k in ("без", "учёта", "ограничений", "мин.", "максимальный")
                ):
                    return idx
            # Fallback to substring
            for header, idx in header_map.items():
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
