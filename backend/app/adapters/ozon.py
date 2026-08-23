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

    def _extract_order_date(self, item: Dict[str, Any], fallback: datetime) -> datetime:
        """Try several date fields; FBO uses created_at, FBS uses in_process_at."""
        for key in ("created_at", "in_process_at", "shipment_date", "delivering_date"):
            value = item.get(key)
            if value:
                parsed = self._parse_date(value, fallback)
                if parsed is not fallback:
                    return parsed
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
                            "since": current.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                            "to": chunk_end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                        },
                        "limit": limit,
                        "offset": offset,
                        "with": {"analytics_data": True},
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

    def _extract_posting_expenses(self, item: Dict[str, Any], product: Dict[str, Any]) -> Dict[str, Decimal]:
        """Extract per-product expenses from Ozon posting analytics_data."""
        analytics = item.get("analytics_data") or {}
        financial = analytics.get("financial_data") or {}

        # Find product-specific financial row by sku/offer_id if present
        offer_id = str(product.get("offer_id", ""))
        product_financial = None
        for fp in financial.get("products", []) or []:
            if str(fp.get("offer_id", "")) == offer_id or str(fp.get("sku", "")) == offer_id:
                product_financial = fp
                break

        def get(*keys):
            for src in ([product_financial] if product_financial else []) + [financial, analytics]:
                if not src:
                    continue
                for key in keys:
                    val = src.get(key)
                    if val is not None:
                        return Decimal(str(val))
            return Decimal(0)

        return {
            "commission": get("commission_amount", "commission"),
            "logistics": get("delivery_rub", "delivery_amount", "logistics_amount", "logistics"),
            "storage": get("storage_amount", "storage"),
            "returns": get("return_amount", "returns", "refund_amount"),
            "insurance": get("insurance_amount", "insurance"),
            "acquiring": get("acquiring_amount", "acquiring"),
            "other": get("picking_amount", "price_service_amount", "other"),
        }

    async def get_orders(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
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
                price = Decimal(str(product.get("price", "0") or "0"))
                orders.append({
                    "date": created_at,
                    "external_sku": str(product.get("offer_id", "")),
                    "external_id": posting_number,
                    "quantity": quantity,
                    "price": price,
                    "revenue": price * quantity,
                    "status": item.get("status", ""),
                    **expenses,
                })

        fbs_items = await self._fetch_postings(
            "/v3/posting/fbs/list", date_from, date_to, result_key="postings"
        )
        for item in fbs_items:
            created_at = self._extract_order_date(item, date_from)
            posting_number = str(item.get("posting_number", "") or item.get("id", ""))
            for product in item.get("products", []):
                expenses = self._extract_posting_expenses(item, product)
                quantity = product.get("quantity", 1)
                price = Decimal(str(product.get("price", "0") or "0"))
                orders.append({
                    "date": created_at,
                    "external_sku": str(product.get("offer_id", "")),
                    "external_id": posting_number,
                    "quantity": quantity,
                    "price": price,
                    "revenue": price * quantity,
                    "status": item.get("status", ""),
                    **expenses,
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
            logger.warning("Failed to get Ozon balance for shop %s: %s", self.shop_id, e)
            return None

    async def get_finance_report(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Fetch transactions from Ozon and group expenses by posting number.

        Ozon /v3/finance/transaction/list returns accruals that match the
        Finance → Accruals section in the seller dashboard. We map each
        operation to an expense category and later distribute the amounts
        across the SKU rows of the same posting number.
        """
        page = 1
        page_size = 1000
        operations = []

        while True:
            data = await self._post_with_delay(
                "/v3/finance/transaction/list",
                {
                    "filter": {
                        "date": {
                            "from": date_from.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                            "to": date_to.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                        },
                        "operation_type": [],
                        "posting_number": "",
                        "transaction_type": "all",
                    },
                    "page": page,
                    "page_size": page_size,
                },
            )
            result = data.get("result", {})
            ops = result.get("operations", [])
            operations.extend(ops)
            page_count = result.get("page_count", 1)
            if page >= page_count or not ops:
                break
            page += 1

        logger.info("Ozon transactions fetched: %d operations", len(operations))

        expenses_by_posting: Dict[str, Dict[str, Decimal]] = {}

        def service_amount_by_name(services: List[Dict[str, Any]], *keywords: str) -> Decimal:
            total = Decimal("0")
            for service in services or []:
                name = (service.get("name") or "").lower()
                if any(kw in name for kw in keywords):
                    price = Decimal(str(service.get("price", 0) or 0))
                    total += abs(price)
            return total

        for op in operations:
            op_type = (op.get("operation_type") or "").lower()
            name = (op.get("operation_type_name") or "").lower()
            amount = Decimal(str(op.get("amount", 0) or 0))
            posting = op.get("posting") or {}
            posting_number = str(posting.get("posting_number", ""))
            services = op.get("services") or []

            # Delivery to customer: commission and logistics are broken out
            # in sale_commission and services. ``amount`` itself is usually
            # positive (net payout) and must not be treated as an expense.
            if op_type == "operationagentdeliveredtocustomer":
                if not posting_number:
                    continue
                bucket = expenses_by_posting.setdefault(posting_number, {
                    "commission": Decimal("0"),
                    "logistics": Decimal("0"),
                    "storage": Decimal("0"),
                    "advertising": Decimal("0"),
                    "returns": Decimal("0"),
                    "insurance": Decimal("0"),
                    "acquiring": Decimal("0"),
                    "other": Decimal("0"),
                })
                bucket["commission"] += abs(Decimal(str(op.get("sale_commission", 0) or 0)))
                bucket["logistics"] += service_amount_by_name(
                    services,
                    "logistic", "lastmile", "handoverplace", "deliverytohandover",
                )
                bucket["returns"] += service_amount_by_name(services, "return")
                bucket["storage"] += service_amount_by_name(services, "storage")
                bucket["advertising"] += service_amount_by_name(services, "advert", "marketing")
                bucket["insurance"] += service_amount_by_name(services, "insurance")
                bucket["acquiring"] += service_amount_by_name(services, "acquiring")
                continue

            # Skip accruals that are not expenses (positive amount, no posting).
            if amount >= 0:
                continue

            # For postings without a number, we currently cannot reliably map
            # SKU-level charges (e.g. insurance) to our sales rows, so skip them.
            if not posting_number:
                continue

            bucket = expenses_by_posting.setdefault(posting_number, {
                "commission": Decimal("0"),
                "logistics": Decimal("0"),
                "storage": Decimal("0"),
                "advertising": Decimal("0"),
                "returns": Decimal("0"),
                "insurance": Decimal("0"),
                "acquiring": Decimal("0"),
                "other": Decimal("0"),
            })

            lowered_name = name
            if op_type in ("clientreturnagentoperation", "operationreturngoodsfbsofrms") or "return" in lowered_name:
                bucket["returns"] += abs(amount)
            elif (
                op_type
                in (
                    "marketplacemarketingactioncostoperation",
                    "operationmarketplacecostperclick",
                    "operationpromotionwithcostperorder",
                )
                or "marketing" in lowered_name
                or "реклама" in lowered_name
                or "оплата за клик" in lowered_name
                or "продвижение с оплатой за заказ" in lowered_name
            ):
                bucket["advertising"] += abs(amount)
            elif "insurance" in lowered_name:
                bucket["insurance"] += abs(amount)
            elif "acquiring" in lowered_name or "эквайринг" in lowered_name:
                bucket["acquiring"] += abs(amount)
            else:
                # Compensations and any other charges.
                bucket["other"] += abs(amount)

        return [
            {
                "external_id": posting_number,
                **amounts,
            }
            for posting_number, amounts in expenses_by_posting.items()
        ]