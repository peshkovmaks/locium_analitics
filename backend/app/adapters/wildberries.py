"""Wildberries API Adapter.

API docs: https://dev.wildberries.ru/
Auth: API token in header 'Authorization: <token>'
Base URL: https://statistics-api.wildberries.ru (for stats)
          https://marketplace-api.wildberries.ru (for marketplace)
          https://advert-api.wildberries.ru (for adverts)

Key endpoints:
- GET /api/v1/supplier/sales — sales data
- GET /api/v1/supplier/orders — orders
- GET /api/v1/supplier/stocks — stocks
- GET /api/v5/supplier/reportDetailByPeriod — finance report
- POST /adv/v1/promotion/adverts — advert campaigns
- POST /adv/v2/fullstats — advert stats
"""

import httpx
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from decimal import Decimal

from app.adapters.base import MarketplaceAdapter


class WildberriesAdapter(MarketplaceAdapter):
    """Adapter for Wildberries API."""

    BASE_URLS = {
        "statistics": "https://statistics-api.wildberries.ru",
        "marketplace": "https://marketplace-api.wildberries.ru",
        "advert": "https://advert-api.wildberries.ru",
    }

    def __init__(self, shop_id: str, credentials: Dict[str, Any]):
        super().__init__(shop_id, credentials)
        self.api_key = credentials.get("api_key", "")
        self.headers = {"Authorization": self.api_key}

    async def authenticate(self) -> bool:
        """Check if API key is valid by making a test request."""
        try:
            async with httpx.AsyncClient() as client:
                # Try to get stocks as a lightweight test
                response = await client.get(
                    f"{self.BASE_URLS['marketplace']}/api/v3/stocks",
                    headers=self.headers,
                    params={"limit": 1},
                    timeout=10.0,
                )
                return response.status_code == 200
        except Exception:
            return False

    async def _get(self, endpoint: str, base: str = "statistics", params: Optional[Dict] = None) -> Any:
        """Make GET request to WB API."""
        url = f"{self.BASE_URLS[base]}{endpoint}"
        async with httpx.AsyncClient() as client:
            response = await client.get(url, headers=self.headers, params=params, timeout=30.0)
            response.raise_for_status()
            return response.json()

    async def _post(self, endpoint: str, base: str = "advert", data: Optional[Dict] = None) -> Any:
        """Make POST request to WB API."""
        url = f"{self.BASE_URLS[base]}{endpoint}"
        async with httpx.AsyncClient() as client:
            response = await client.post(url, headers=self.headers, json=data, timeout=30.0)
            response.raise_for_status()
            return response.json()

    async def get_sales(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Get sales data from WB.

        Returns list of sales with fields:
        - date, lastChangeDate, supplierArticle, techSize, barcode,
        - totalPrice, discountPercent, isSupply, isRealization,
        - promoCodeDiscount, warehouseName, countryName, oblastOkrugName,
        - regionName, incomeID, saleID, odid, spp, forPay, finishedPrice,
        - priceWithDisc, nmId, subject, category, brand, IsStorno, gNumber
        """
        # WB API requires date in format YYYY-MM-DD
        date_from_str = date_from.strftime("%Y-%m-%d")
        date_to_str = date_to.strftime("%Y-%m-%d")

        data = await self._get(
            "/api/v1/supplier/sales",
            params={"dateFrom": date_from_str, "dateTo": date_to_str, "flag": 0},
        )

        sales = []
        for item in data:
            sales.append({
                "date": datetime.fromisoformat(item.get("date", "").replace("Z", "+00:00")),
                "external_sku": item.get("supplierArticle", ""),
                "external_id": str(item.get("nmId", "")),
                "quantity": 1 if not item.get("IsStorno", False) else -1,
                "price": Decimal(str(item.get("finishedPrice", 0) or item.get("totalPrice", 0))),
                "revenue": Decimal(str(item.get("forPay", 0))),
                "commission": Decimal("0"),  # Will be calculated from finance report
                "logistics": Decimal("0"),
                "storage": Decimal("0"),
                "advertising": Decimal("0"),
                "returns": Decimal("0"),
                "other": Decimal("0"),
                "is_return": item.get("IsStorno", False),
            })
        return sales

    async def get_orders(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Get orders data."""
        date_from_str = date_from.strftime("%Y-%m-%d")
        date_to_str = date_to.strftime("%Y-%m-%d")

        data = await self._get(
            "/api/v1/supplier/orders",
            params={"dateFrom": date_from_str, "dateTo": date_to_str, "flag": 0},
        )

        orders = []
        for item in data:
            orders.append({
                "date": datetime.fromisoformat(item.get("date", "").replace("Z", "+00:00")),
                "external_sku": item.get("supplierArticle", ""),
                "external_id": str(item.get("nmId", "")),
                "quantity": item.get("quantity", 1),
                "price": Decimal(str(item.get("totalPrice", 0))),
                "status": "ordered",
            })
        return orders

    async def get_stocks(self) -> List[Dict[str, Any]]:
        """Get current stock levels."""
        data = await self._get(
            "/api/v1/supplier/stocks",
            params={"limit": 1000},
        )

        stocks = []
        for item in data:
            stocks.append({
                "external_sku": item.get("supplierArticle", ""),
                "external_id": str(item.get("nmId", "")),
                "warehouse": item.get("warehouseName", "Unknown"),
                "quantity": item.get("quantity", 0),
                "in_way": item.get("inWayToClient", 0) + item.get("inWayFromClient", 0),
            })
        return stocks

    async def get_adverts(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Get advertising campaigns and stats."""
        # First, get list of campaigns
        campaigns = await self._get("/adv/v1/promotion/adverts", base="advert")

        adverts = []
        if not campaigns:
            return adverts

        # Get full stats for each campaign
        campaign_ids = [c.get("advertId") for c in campaigns if c.get("advertId")]

        if campaign_ids:
            stats = await self._post(
                "/adv/v2/fullstats",
                base="advert",
                data={
                    "id": campaign_ids,
                    "dates": {
                        "from": date_from.strftime("%Y-%m-%d"),
                        "to": date_to.strftime("%Y-%m-%d"),
                    },
                },
            )

            for stat in stats:
                for day_stat in stat.get("days", []):
                    adverts.append({
                        "date": datetime.strptime(day_stat.get("date", ""), "%Y-%m-%d"),
                        "campaign_id": str(stat.get("advertId", "")),
                        "external_sku": "",  # WB stats don't always include SKU per day
                        "views": day_stat.get("views", 0),
                        "clicks": day_stat.get("clicks", 0),
                        "ctr": Decimal(str(day_stat.get("ctr", 0))),
                        "cpc": Decimal(str(day_stat.get("cpc", 0))),
                        "spend": Decimal(str(day_stat.get("sum", 0))),
                        "orders": day_stat.get("orders", 0),
                        "cr": Decimal(str(day_stat.get("cr", 0))),
                    })

        return adverts

    async def get_prices(self) -> List[Dict[str, Any]]:
        """Get current prices on WB."""
        data = await self._get(
            "/api/v2/list/goods/filter",
            base="marketplace",
            params={"limit": 1000},
        )

        prices = []
        for item in data.get("data", {}).get("list", []):
            prices.append({
                "external_sku": item.get("vendorCode", ""),
                "external_id": str(item.get("nmID", "")),
                "price": Decimal(str(item.get("price", 0))),
                "discount": item.get("discount", 0),
            })
        return prices

    async def get_finance_report(self, date_from: datetime, date_to: datetime) -> List[Dict[str, Any]]:
        """Get detailed finance report.

        This is the key method for calculating real expenses (commission, logistics, etc.)
        """
        # WB requires date in format YYYY-MM-DD
        date_from_str = date_from.strftime("%Y-%m-%d")
        date_to_str = date_to.strftime("%Y-%m-%d")

        # Get realization reports
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
                "price": Decimal(str(item.get("retail_price", 0))),
                "revenue": Decimal(str(item.get("retail_amount", 0))),
                "commission": Decimal(str(item.get("commission_amount", 0))),
                "logistics": Decimal(str(item.get("delivery_rub", 0))),
                "storage": Decimal(str(item.get("storage_fee", 0))),
                "returns": Decimal(str(item.get("return_amount", 0))),
                "other": Decimal(str(item.get("deduction", 0))),
            })
        return reports
