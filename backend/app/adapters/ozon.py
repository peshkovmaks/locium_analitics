"""Ozon Seller API Adapter."""

import os
import httpx
import logging
import asyncio
from datetime import datetime, timedelta, timezone
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

    def _http_client(self) -> httpx.AsyncClient:
        proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
        return httpx.AsyncClient(proxy=proxy) if proxy else httpx.AsyncClient()

    async def authenticate(self) -> bool:
        try:
            async with self._http_client() as client:
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
        async with self._http_client() as client:
            response = await client.post(
                url, headers=self.headers, json=data or {}, timeout=30.0
            )
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

    def _extract_order_date(self, item: Dict[str, Any], fallback: datetime) -> datetime:
        """Try several date fields; FBO uses created_at, FBS uses in_process_at."""
        for key in ("created_at", "in_process_at", "shipment_date", "delivering_date"):
            value = item.get(key)
            if value:
                parsed = self._parse_date(value, fallback)
                if parsed is not fallback:
                    return parsed
        return fallback

    async def get_sales(
        self, date_from: datetime, date_to: datetime
    ) -> List[Dict[str, Any]]:
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

            sales.append(
                {
                    "date": (
                        datetime.strptime(dimensions.get("day", ""), "%Y-%m-%d")
                        if dimensions.get("day")
                        else date_from
                    ),
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

    async def _fetch_postings(
        self,
        endpoint: str,
        date_from: datetime,
        date_to: datetime,
        result_key: str,
    ) -> List[Dict[str, Any]]:
        """Fetch all postings for one endpoint with pagination.

        Ozon posting endpoints reject date ranges longer than ~90 days, so the
        request is split into chunks when needed.
        """
        chunk_days = 90
        limit = 1000
        all_items = []

        current = date_from
        while current < date_to:
            chunk_end = min(current + timedelta(days=chunk_days), date_to)
            offset = 0
            while True:
                data = await self._post_with_delay(
                    endpoint,
                    {
                        "dir": "ASC",
                        "filter": {
                            "since": current.astimezone(timezone.utc)
                            .isoformat()
                            .replace("+00:00", "Z"),
                            "to": chunk_end.astimezone(timezone.utc)
                            .isoformat()
                            .replace("+00:00", "Z"),
                        },
                        "limit": limit,
                        "offset": offset,
                        "with": {"analytics_data": True, "financial_data": True},
                    },
                )

                if result_key:
                    items = data.get("result", {}).get(result_key, [])
                else:
                    items = data.get("result", [])

                if not items:
                    break
                all_items.extend(items)
                if len(items) < limit:
                    break
                offset += limit

            current = chunk_end

        return all_items

    def _get_product_financial(
        self, item: Dict[str, Any], product: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Return the financial_data row matching this product.

        Ozon uses ``product_id`` inside financial_data, which corresponds to
        the ``sku`` field (Ozon product id) in the posting's product object.
        """
        financial = item.get("financial_data") or {}
        offer_id = str(product.get("offer_id", ""))
        sku = product.get("sku")
        for fp in financial.get("products", []) or []:
            fp_offer_id = str(fp.get("offer_id", ""))
            fp_sku = fp.get("sku")
            fp_product_id = fp.get("product_id")
            if (
                (offer_id and fp_offer_id and fp_offer_id == offer_id)
                or (sku is not None and fp_sku is not None and str(fp_sku) == str(sku))
                or (
                    sku is not None
                    and fp_product_id is not None
                    and str(fp_product_id) == str(sku)
                )
            ):
                return fp
        return None

    def _extract_posting_expenses(
        self, item: Dict[str, Any], product: Dict[str, Any]
    ) -> Dict[str, Decimal]:
        """Extract per-product expenses from Ozon posting financial_data."""
        analytics = item.get("analytics_data") or {}
        financial = item.get("financial_data") or {}
        product_financial = self._get_product_financial(item, product)

        def get(*keys):
            for src in ([product_financial] if product_financial else []) + [
                financial,
                analytics,
            ]:
                if not src:
                    continue
                for key in keys:
                    val = src.get(key)
                    if val is not None:
                        return max(Decimal(str(val)), Decimal(0))
            return Decimal(0)

        return {
            "commission": get("commission_amount", "commission"),
            "logistics": get(
                "delivery_rub", "delivery_amount", "logistics_amount", "logistics"
            ),
            "storage": get("storage_amount", "storage"),
            "returns": get("return_amount", "returns", "refund_amount"),
            "insurance": get("insurance_amount", "insurance"),
            "acquiring": get("acquiring_amount", "acquiring"),
            "other": get("picking_amount", "price_service_amount", "other"),
        }

    async def get_orders(
        self, date_from: datetime, date_to: datetime
    ) -> List[Dict[str, Any]]:
        orders = []

        fbo_items = await self._fetch_postings(
            "/v2/posting/fbo/list", date_from, date_to, result_key=""
        )
        for item in fbo_items:
            created_at = self._extract_order_date(item, date_from)
            posting_number = str(item.get("posting_number", "") or item.get("id", ""))
            for product in item.get("products", []):
                expenses = self._extract_posting_expenses(item, product)
                quantity = product.get("quantity", 1)
                pf = self._get_product_financial(item, product)
                # Use financial_data price when available; fallback to posting price.
                price = Decimal(
                    str(
                        pf.get("price", product.get("price", "0"))
                        if pf
                        else product.get("price", "0") or "0"
                    )
                )
                customer_price = (
                    Decimal(str(pf.get("customer_price", 0) or 0)) if pf else Decimal(0)
                )
                marketplace_discount = (
                    max(price - customer_price, Decimal(0)) * quantity
                )
                orders.append(
                    {
                        "date": created_at,
                        "external_sku": str(product.get("offer_id", "")),
                        "external_id": posting_number,
                        "quantity": quantity,
                        "price": price,
                        "customer_price": customer_price,
                        "marketplace_discount": marketplace_discount,
                        "revenue": price * quantity,
                        "status": item.get("status", ""),
                        **expenses,
                    }
                )

        fbs_items = await self._fetch_postings(
            "/v3/posting/fbs/list", date_from, date_to, result_key="postings"
        )
        for item in fbs_items:
            created_at = self._extract_order_date(item, date_from)
            posting_number = str(item.get("posting_number", "") or item.get("id", ""))
            for product in item.get("products", []):
                expenses = self._extract_posting_expenses(item, product)
                quantity = product.get("quantity", 1)
                pf = self._get_product_financial(item, product)
                price = Decimal(
                    str(
                        pf.get("price", product.get("price", "0"))
                        if pf
                        else product.get("price", "0") or "0"
                    )
                )
                customer_price = (
                    Decimal(str(pf.get("customer_price", 0) or 0)) if pf else Decimal(0)
                )
                marketplace_discount = (
                    max(price - customer_price, Decimal(0)) * quantity
                )
                orders.append(
                    {
                        "date": created_at,
                        "external_sku": str(product.get("offer_id", "")),
                        "external_id": posting_number,
                        "quantity": quantity,
                        "price": price,
                        "customer_price": customer_price,
                        "marketplace_discount": marketplace_discount,
                        "revenue": price * quantity,
                        "status": item.get("status", ""),
                        **expenses,
                    }
                )

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

                stocks.append(
                    {
                        "external_sku": str(item.get("offer_id", "")),
                        "external_id": str(item.get("product_id", "")),
                        "warehouse": (
                            stock_entries[0].get("warehouse_name", "FBS")
                            if stock_entries
                            else "FBS"
                        ),
                        "quantity": total_present,
                        "in_way": total_reserved,
                    }
                )
            return stocks
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(
                    "Ozon /v3/product/info/stocks returned 404, skipping stocks"
                )
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
                logger.warning(
                    "Ozon /v3/product/info/list returned 404, names unavailable"
                )
            else:
                logger.warning("Ozon /v3/product/info/list failed: %s", e)
            return {}

    async def get_adverts(
        self, date_from: datetime, date_to: datetime
    ) -> List[Dict[str, Any]]:
        logger.warning(
            "Ozon ads skipped: requires Performance API (OAuth). Seller API does not provide campaign endpoints."
        )
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
                prices.append(
                    {
                        "external_sku": str(item.get("offer_id", "")),
                        "external_id": str(item.get("sku", "")),
                        "price": Decimal(str(price_val or 0)),
                        "discount": item.get("discount", 0),
                    }
                )
            return prices
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                logger.warning(
                    "Ozon /v5/product/info/prices returned 404, skipping prices"
                )
                return []
            raise

    async def get_balance(self) -> Optional[Dict[str, Any]]:
        """Get seller balance from Ozon Finance → Balance report.

        Uses POST /v1/finance/balance. The API does not expose a dedicated
        "available for withdrawal" amount, so we use the closing balance for the
        current day as the best proxy. payout_at is not provided by Ozon API.
        """
        try:
            today = datetime.utcnow().date()
            data = await self._post_with_delay(
                "/v1/finance/balance",
                {
                    "date_from": today.strftime("%Y-%m-%d"),
                    "date_to": today.strftime("%Y-%m-%d"),
                },
            )
            total = (data or {}).get("total", {})
            closing = total.get("closing_balance", {}) or {}
            return {
                "balance": Decimal(str(closing.get("value", 0) or 0)),
                "available": Decimal(str(closing.get("value", 0) or 0)),
                "currency": closing.get("currency_code", "RUB"),
                "payout_at": None,
            }
        except Exception as e:
            logger.warning(
                "Failed to get Ozon balance for shop %s: %s", self.shop_id, e
            )
            return None

    # Fee type_id -> expense category, from GET /v1/finance/accrual/types.
    _FEE_TYPE_CATEGORIES = {
        # commission
        8: "commission",  # ClaimCommission
        63: "commission",  # RfbsDomesticAgentFee
        66: "commission",  # RfbsGlobalAgentFee
        68: "commission",  # RfbsServiceFee
        69: "commission",  # SaleCommission
        # logistics
        2: "logistics",
        13: "logistics",
        16: "logistics",
        17: "logistics",
        28: "logistics",
        29: "logistics",
        30: "logistics",
        32: "logistics",
        42: "logistics",
        43: "logistics",
        44: "logistics",
        56: "logistics",
        64: "logistics",
        67: "logistics",
        73: "logistics",
        88: "logistics",
        97: "logistics",
        98: "logistics",
        99: "logistics",
        100: "logistics",
        106: "logistics",
        107: "logistics",
        110: "logistics",
        111: "logistics",
        112: "logistics",
        114: "logistics",
        115: "logistics",
        120: "logistics",
        121: "logistics",
        # storage
        46: "storage",
        58: "storage",
        60: "storage",
        78: "storage",
        79: "storage",
        102: "storage",
        # returns
        9: "returns",
        40: "returns",
        45: "returns",
        53: "returns",
        59: "returns",
        65: "returns",
        113: "returns",
        # advertising
        3: "advertising",
        4: "advertising",
        5: "advertising",
        19: "advertising",
        23: "advertising",
        31: "advertising",
        33: "advertising",
        36: "advertising",
        41: "advertising",
        47: "advertising",
        49: "advertising",
        50: "advertising",
        51: "advertising",
        52: "advertising",
        54: "advertising",
        55: "advertising",
        61: "advertising",
        70: "advertising",
        74: "advertising",
        75: "advertising",
        80: "advertising",
        87: "advertising",
        95: "advertising",
        96: "advertising",
        116: "advertising",
        117: "advertising",
        118: "advertising",
        119: "advertising",
        # acquiring / insurance
        1: "acquiring",
        76: "insurance",
        104: "insurance",
        105: "insurance",
    }

    def _category_for_fee_type(self, type_id: Any) -> str:
        try:
            tid = int(type_id)
        except (TypeError, ValueError):
            return "other"
        return self._FEE_TYPE_CATEGORIES.get(tid, "other")

    @staticmethod
    def _money(value: Any) -> Decimal:
        if isinstance(value, dict):
            value = value.get("amount", 0)
        try:
            return Decimal(str(value or 0))
        except ArithmeticError:
            return Decimal("0")

    async def get_finance_report(
        self, date_from: datetime, date_to: datetime
    ) -> List[Dict[str, Any]]:
        """Fetch daily accruals from Ozon and return normalized expense rows.

        Ozon disabled /v3/finance/transaction/list (September 2026). Its
        replacement, /v1/finance/accrual/by-day, returns per-day accruals of
        three kinds:
          - POSTING: one per delivered posting, with per-product commission
            and delivery services;
          - ITEM: per-SKU fees (e.g. acquiring);
          - NON_ITEM: shop-level fees (e.g. stock insurance).
        Fees are typed by ``type_id`` (see /v1/finance/accrual/types); we map
        them to dashboard categories while preserving the original sign.
        Positive credits are required for a correct seller-level profit.

        The endpoint accepts a single calendar day per request and paginates
        via an opaque ``last_id`` token, so we iterate day by day.
        """
        aggregated: Dict[tuple, Dict[str, Any]] = {}

        def add_fee(
            day: datetime,
            posting_number: Optional[str],
            sku: Optional[str],
            category: str,
            amount: Decimal,
            type_id: Any,
            accrual_id: Any,
            op_kind: str,
        ) -> None:
            key = (posting_number, sku, category, op_kind)
            row = aggregated.get(key)
            if row is None:
                row = {
                    "operation_date": day,
                    "posting_number": posting_number,
                    "external_sku": sku,
                    "operation_type": op_kind,
                    "operation_name": f"type_{type_id}",
                    "category": category,
                    "amount": Decimal("0"),
                    "raw": {
                        "accrual_id": accrual_id,
                        "type_id": type_id,
                        "signed": True,
                    },
                }
                aggregated[key] = row
            row["amount"] += amount

        def walk_fees(
            fees: Any,
            day: datetime,
            posting_number: Optional[str],
            sku: Optional[str],
            accrual_id: Any,
            op_kind: str,
        ) -> None:
            for fee in fees or []:
                if not isinstance(fee, dict):
                    continue
                type_id = fee.get("type_id")
                amount = self._money(fee.get("accrued"))
                add_fee(
                    day,
                    posting_number,
                    sku,
                    self._category_for_fee_type(type_id),
                    amount,
                    type_id,
                    accrual_id,
                    op_kind,
                )

        day = date_from.date() if isinstance(date_from, datetime) else date_from
        last_day = date_to.date() if isinstance(date_to, datetime) else date_to
        accrual_count = 0

        while day <= last_day:
            last_id: Optional[str] = None
            while True:
                payload: Dict[str, Any] = {
                    "date": day.strftime("%Y-%m-%d"),
                    "limit": 1000,
                }
                if last_id:
                    payload["last_id"] = last_id
                data = await self._post_with_delay(
                    "/v1/finance/accrual/by-day", payload
                )
                accruals = data.get("accruals") or []
                accrual_count += len(accruals)
                new_last_id = data.get("last_id")

                for acc in accruals:
                    op_kind = acc.get("accrued_category") or "UNKNOWN"
                    accrual_id = acc.get("accrual_id")
                    posting_number = acc.get("unit_number") or None
                    day_dt = datetime.combine(day, datetime.min.time())

                    posting = acc.get("posting") or {}
                    for product in posting.get("products") or []:
                        sku = str(product.get("sku", "")) or None
                        commission = self._money(
                            (product.get("commission") or {}).get("sale_commission")
                        )
                        add_fee(
                            day_dt,
                            posting_number,
                            sku,
                            "commission",
                            commission,
                            69,
                            accrual_id,
                            op_kind,
                        )
                        delivery = product.get("delivery") or {}
                        walk_fees(
                            delivery.get("services"),
                            day_dt,
                            posting_number,
                            sku,
                            accrual_id,
                            op_kind,
                        )

                    for group in (acc.get("item_fees") or {}).get("fees") or []:
                        sku = str(group.get("sku", "")) or None
                        walk_fees(
                            group.get("fees"),
                            day_dt,
                            posting_number,
                            sku,
                            accrual_id,
                            op_kind,
                        )

                    non_item = acc.get("non_item_fee")
                    if isinstance(non_item, dict):
                        type_id = non_item.get("type_id")
                        amount = self._money(non_item.get("accrued"))
                        add_fee(
                            day_dt,
                            posting_number,
                            None,
                            self._category_for_fee_type(type_id),
                            amount,
                            type_id,
                            accrual_id,
                            op_kind,
                        )

                    container = acc.get("container_fees")
                    if isinstance(container, dict):
                        walk_fees(
                            container.get("fees"),
                            day_dt,
                            posting_number,
                            None,
                            accrual_id,
                            op_kind,
                        )

                if not accruals or not new_last_id or new_last_id == last_id:
                    break
                last_id = new_last_id

            day += timedelta(days=1)

        logger.info(
            "Ozon accruals fetched: %d accruals, %d expense rows",
            accrual_count,
            len(aggregated),
        )
        return list(aggregated.values())
