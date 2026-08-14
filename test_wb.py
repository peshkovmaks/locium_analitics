#!/usr/bin/env python3
"""
Standalone test script for Wildberries API.

Usage:
    python test_wb.py --api-key YOUR_API_KEY

What it tests:
1. Authentication (marketplace/api/v3/stocks)
2. Sales (statistics/api/v1/supplier/sales)
3. Orders (statistics/api/v1/supplier/orders)
4. Stocks (statistics/api/v1/supplier/stocks)
5. Prices (marketplace/api/v2/list/goods/filter)
6. Finance report (statistics/api/v5/supplier/reportDetailByPeriod)
7. Ad campaigns (advert/adv/v1/promotion/adverts)

Output: saves results to wb_test_results.json
"""

import argparse
import json
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import httpx


class WildberriesTester:
    BASE_URLS = {
        "statistics": "https://statistics-api.wildberries.ru",
        "marketplace": "https://marketplace-api.wildberries.ru",
        "advert": "https://advert-api.wildberries.ru",
    }

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.headers = {"Authorization": api_key}
        self.results = {}

    async def _get(self, endpoint: str, base: str = "statistics", params: dict = None) -> dict:
        url = f"{self.BASE_URLS[base]}{endpoint}"
        async with httpx.AsyncClient() as client:
            try:
                response = await client.get(url, headers=self.headers, params=params, timeout=30.0)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                return {
                    "error": True,
                    "status_code": e.response.status_code,
                    "message": str(e),
                    "response": e.response.text[:500],
                }
            except Exception as e:
                return {"error": True, "message": str(e)}

    async def _post(self, endpoint: str, base: str = "advert", data: dict = None) -> dict:
        url = f"{self.BASE_URLS[base]}{endpoint}"
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, headers=self.headers, json=data, timeout=30.0)
                response.raise_for_status()
                return response.json()
            except httpx.HTTPStatusError as e:
                return {
                    "error": True,
                    "status_code": e.response.status_code,
                    "message": str(e),
                    "response": e.response.text[:500],
                }
            except Exception as e:
                return {"error": True, "message": str(e)}

    async def test_auth(self):
        print("\n🧪 Test 1: Authentication (marketplace/api/v3/stocks)")
        result = await self._get("/api/v3/stocks", base="marketplace", params={"limit": 1})

        if result.get("error"):
            print(f"   ❌ FAILED: {result.get('status_code')} - {result.get('message')}")
            self.results["auth"] = result
            return False

        print("   ✅ OK! API key is valid")
        self.results["auth"] = {"success": True}
        return True

    async def test_sales(self):
        print("\n🧪 Test 2: Sales (statistics/api/v1/supplier/sales)")
        date_to = datetime.now(timezone.utc)
        date_from = date_to - timedelta(days=7)

        result = await self._get(
            "/api/v1/supplier/sales",
            params={
                "dateFrom": date_from.strftime("%Y-%m-%d"),
                "dateTo": date_to.strftime("%Y-%m-%d"),
                "flag": 0,
            },
        )

        if result.get("error"):
            print(f"   ❌ FAILED: {result.get('message')}")
            self.results["sales"] = result
            return

        items = result if isinstance(result, list) else []
        print(f"   ✅ OK! Found {len(items)} sales records")

        for item in items[:5]:
            sku = item.get("supplierArticle", "N/A")
            price = item.get("finishedPrice", item.get("totalPrice", 0))
            qty = 1 if not item.get("IsStorno", False) else -1
            print(f"   • SKU: {sku} | Price: {price}₽ | Qty: {qty}")

        self.results["sales"] = {"count": len(items), "sample": items[:5]}

    async def test_orders(self):
        print("\n🧪 Test 3: Orders (statistics/api/v1/supplier/orders)")
        date_to = datetime.now(timezone.utc)
        date_from = date_to - timedelta(days=7)

        result = await self._get(
            "/api/v1/supplier/orders",
            params={
                "dateFrom": date_from.strftime("%Y-%m-%d"),
                "dateTo": date_to.strftime("%Y-%m-%d"),
                "flag": 0,
            },
        )

        if result.get("error"):
            print(f"   ❌ FAILED: {result.get('message')}")
            self.results["orders"] = result
            return

        items = result if isinstance(result, list) else []
        print(f"   ✅ OK! Found {len(items)} orders")

        for item in items[:5]:
            sku = item.get("supplierArticle", "N/A")
            price = item.get("totalPrice", 0)
            qty = item.get("quantity", 1)
            print(f"   • SKU: {sku} | Price: {price}₽ | Qty: {qty}")

        self.results["orders"] = {"count": len(items), "sample": items[:5]}

    async def test_stocks(self):
        print("\n🧪 Test 4: Stocks (statistics/api/v1/supplier/stocks)")
        result = await self._get("/api/v1/supplier/stocks", params={"limit": 50})

        if result.get("error"):
            print(f"   ❌ FAILED: {result.get('message')}")
            self.results["stocks"] = result
            return

        items = result if isinstance(result, list) else []
        print(f"   ✅ OK! Found {len(items)} stock records")

        for item in items[:5]:
            sku = item.get("supplierArticle", "N/A")
            qty = item.get("quantity", 0)
            wh = item.get("warehouseName", "Unknown")
            print(f"   • SKU: {sku} | Qty: {qty} | Warehouse: {wh}")

        self.results["stocks"] = {"count": len(items), "sample": items[:5]}

    async def test_prices(self):
        print("\n🧪 Test 5: Prices (marketplace/api/v2/list/goods/filter)")
        result = await self._get("/api/v2/list/goods/filter", base="marketplace", params={"limit": 50})

        if result.get("error"):
            print(f"   ❌ FAILED: {result.get('message')}")
            self.results["prices"] = result
            return

        items = result.get("data", {}).get("list", []) if isinstance(result, dict) else []
        print(f"   ✅ OK! Found {len(items)} price records")

        for item in items[:5]:
            sku = item.get("vendorCode", "N/A")
            price = item.get("price", 0)
            print(f"   • SKU: {sku} | Price: {price}₽")

        self.results["prices"] = {"count": len(items), "sample": items[:5]}

    async def test_finance(self):
        print("\n🧪 Test 6: Finance Report (statistics/api/v5/supplier/reportDetailByPeriod)")
        date_to = datetime.now(timezone.utc)
        date_from = date_to - timedelta(days=7)

        result = await self._get(
            "/api/v5/supplier/reportDetailByPeriod",
            params={
                "dateFrom": date_from.strftime("%Y-%m-%d"),
                "dateTo": date_to.strftime("%Y-%m-%d"),
                "limit": 100000,
            },
        )

        if result.get("error"):
            print(f"   ❌ FAILED: {result.get('message')}")
            self.results["finance"] = result
            return

        items = result if isinstance(result, list) else []
        print(f"   ✅ OK! Found {len(items)} finance rows")

        for item in items[:5]:
            sku = item.get("sa_name", "N/A")
            revenue = item.get("retail_amount", 0)
            commission = item.get("commission_amount", 0)
            print(f"   • SKU: {sku} | Revenue: {revenue}₽ | Commission: {commission}₽")

        self.results["finance"] = {"count": len(items), "sample": items[:5]}

    async def test_adverts(self):
        print("\n🧪 Test 7: Ad Campaigns (advert/adv/v1/promotion/adverts)")
        campaigns = await self._get("/adv/v1/promotion/adverts", base="advert")

        if campaigns.get("error"):
            print(f"   ❌ FAILED: {campaigns.get('message')}")
            self.results["adverts"] = campaigns
            return

        campaign_list = campaigns if isinstance(campaigns, list) else []
        print(f"   ✅ OK! Found {len(campaign_list)} campaigns")

        for camp in campaign_list[:5]:
            print(f"   • Campaign {camp.get('advertId')}: {camp.get('name', 'No name')} | Status: {camp.get('status', 'N/A')}")

        # Try to get stats if campaigns exist
        if campaign_list:
            campaign_ids = [c.get("advertId") for c in campaign_list[:5] if c.get("advertId")]
            if campaign_ids:
                await asyncio.sleep(1)
                print("   🧪 Test 7b: Ad Stats (advert/adv/v2/fullstats)")
                stats = await self._post(
                    "/adv/v2/fullstats",
                    base="advert",
                    data={
                        "id": campaign_ids,
                        "dates": {
                            "from": (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d"),
                            "to": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
                        },
                    },
                )
                if stats.get("error"):
                    print(f"   ❌ Stats FAILED: {stats.get('message')}")
                else:
                    stats_list = stats if isinstance(stats, list) else []
                    print(f"   ✅ Stats OK! {len(stats_list)} stat entries")

        self.results["adverts"] = {"count": len(campaign_list), "sample": campaign_list[:5]}

    async def run_all_tests(self):
        print("=" * 60)
        print("Wildberries API Test")
        print("=" * 60)
        print(f"API Key: {self.api_key[:4]}...{self.api_key[-4:]}")

        if not await self.test_auth():
            print("\n❌ Authentication failed. Check your API key.")
            return False

        await asyncio.sleep(1)
        await self.test_sales()
        await asyncio.sleep(1)
        await self.test_orders()
        await asyncio.sleep(1)
        await self.test_stocks()
        await asyncio.sleep(1)
        await self.test_prices()
        await asyncio.sleep(1)
        await self.test_finance()
        await asyncio.sleep(1)
        await self.test_adverts()

        filename = f"wb_test_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(self.results, f, ensure_ascii=False, indent=2, default=str)

        print(f"\n✅ All tests completed!")
        print(f"📄 Results saved to: {filename}")
        return True


def main():
    parser = argparse.ArgumentParser(description="Test Wildberries API")
    parser.add_argument("--api-key", required=True, help="Your WB API Key (Authorization token)")
    args = parser.parse_args()

    tester = WildberriesTester(args.api_key)
    asyncio.run(tester.run_all_tests())


if __name__ == "__main__":
    main()
