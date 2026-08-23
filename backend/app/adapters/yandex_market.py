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
from datetime import datetime, timezone
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

        Uses /v2/reports/united-marketplace-services/generate
        This is async — first request generates report, then we poll for status.
        Returns aggregated expenses per SKU.
        """
        if not self.business_id:
            logger.warning("YM Business ID not available, skipping finance report")
            return []

        try:
            gen_response = await self._post(
                "/v2/reports/united-marketplace-services/generate",
                {
                    "businessId": int(self.business_id) if self.business_id.isdigit() else 0,
                    "dateFrom": date_from.strftime("%Y-%m-%d"),
                    "dateTo": date_to.strftime("%Y-%m-%d"),
                },
            )
        except httpx.HTTPStatusError as e:
            logger.warning("YM finance report generation failed: %s", e)
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

        return await self._download_and_parse_ym_report(file_url)

    async def _download_and_parse_ym_report(self, file_url: str) -> List[Dict[str, Any]]:
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
            "буст продаж": "advertising",
            "доставка покупателю": "logistics",
            "страхование": "insurance",
            "эквайринг": "acquiring",
            "приём платежа покупателя": "other",
            "перевод платежа покупателя": "other",
        }

        try:
            with zipfile.ZipFile(io.BytesIO(content)) as z:
                sheet_names = self._read_sheet_names(z)
                for sheet_file in z.namelist():
                    if not sheet_file.startswith("xl/worksheets/sheet") or not sheet_file.endswith(".xml"):
                        continue

                    rows = self._parse_xlsx_sheet(z, sheet_file)
                    if not rows:
                        continue

                    # Find header row
                    header_row_index = None
                    header_map: Dict[str, int] = {}
                    for idx, row in enumerate(rows):
                        cells = [str(c).strip().lower() for c in row]
                        if "ваш sku" in cells or "ваш sku" in " ".join(cells):
                            header_row_index = idx
                            header_map = {c: i for i, c in enumerate(cells) if c}
                            break

                    if not header_row_index:
                        continue

                    sku_idx = self._find_col(header_map, "ваш sku")
                    amount_idx = self._find_col(header_map, "стоимость услуги")
                    service_idx = self._find_col(header_map, "услуга")
                    if sku_idx is None or amount_idx is None:
                        continue

                    # Determine default category by sheet title when service column
                    # is missing; otherwise each row's service name decides.
                    default_category = None
                    for title_row in rows[:2]:
                        title_text = " ".join(str(c) for c in title_row if c).lower()
                        for key, cat in sheet_categories.items():
                            if key in title_text:
                                default_category = cat
                                break
                        if default_category:
                            break

                    for row in rows[header_row_index + 1:]:
                        if sku_idx >= len(row):
                            continue
                        sku = str(row[sku_idx]).strip()
                        if not sku:
                            continue

                        try:
                            amount = Decimal(str(row[amount_idx] if amount_idx < len(row) else "0").replace(",", "."))
                        except Exception:
                            continue

                        category = default_category
                        if service_idx is not None and service_idx < len(row):
                            service_name = str(row[service_idx]).lower()
                            category = self._categorize_ym_service(service_name, sheet_categories)

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
