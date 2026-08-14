#!/usr/bin/env python3
"""
Standalone test script for Ozon Seller API.

Usage:
    python test_ozon.py --client-id YOUR_CLIENT_ID --api-key your-api-key-here

What it tests:
1. Authentication (v2/warehouse/list)
2. Product list with prices (v5/product/info/prices)
3. Stocks (v3/product/info/stocks)
4. Orders FBO (v2/posting/fbo/list)
5. Orders FBS (v3/posting/fbs/list)
6. Analytics data (v1/analytics/data)
7. Campaign list — SKIPPED (requires Ozon Performance API, separate OAuth)

Output: saves results to ozon_test_results.json
"""

import argparse
import json
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import httpx


class OzonTester:
    BASE_URL = "https://api-seller.ozon.ru"

    def __init__(self, client_id: str, api_key: str):
        self.client_id = client_id
        self.api_key = api_key
        self.headers = {
            "Client-Id": client_id,
            "Api-Key": api_key,
            "Content-Type": "application/json",
        }
        self.results = {}

    async def _post(self, endpoint: str, data: dict = None) -> dict:
        """Make POST request to Ozon API."""
        url = f"{self.BASE_URL}{endpoint}"
        async with httpx.AsyncClient() as client:
            try:
                response = await client.post(url, headers=self.headers, json=data or {}, timeout=30.0)
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
        """Test 1: Check if credentials work."""
        print("\n🧪 Test 1: Authentication (v2/warehouse/list)")
        result = await self._post("/v2/warehouse/list", {})

        if result.get("error"):
            print(f"   ❌ FAILED: {result.get('status_code')} - {result.get('message')}")
            self.results["auth"] = result
            return False

        warehouses = result.get("warehouses", [])
        print(f"   ✅ OK! Found {len(warehouses)} warehouses")
        for wh in warehouses[:3]:
            print(f"   • {wh.get('name', 'Unknown')} (ID: {wh.get('warehouse_id')})")

        self.results["auth"] = {"success": True, "warehouses_count": len(warehouses), "warehouses": warehouses[:3]}
        return True

    async def test_products(self):
        """Test 2: Get product list with prices."""
        print("\n🧪 Test 2: Products + Prices (v5/product/info/prices)")
        result = await self._post("/v5/product/info/prices", {
            "filter": {"visibility": "ALL"},
            "limit": 50,
            "cursor": "",
        })

        if result.get("error"):
            print(f"   ❌ FAILED: {result.get('message')}")
            self.results["products"] = result
            return

        items = result.get("items", [])
        print(f"   ✅ OK! Found {len(items)} products")

        for item in items[:5]:
            sku = item.get("offer_id", "N/A")
            price_info = item.get("price", {})
            price = price_info.get("price", 0) if isinstance(price_info, dict) else item.get("price", 0)
            print(f"   • SKU: {sku} | Price: {price}₽")

        self.results["products"] = {
            "count": len(items),
            "sample": items[:5],
        }

    async def test_stocks(self):
        """Test 3: Get stock levels.

        Uses /v3/product/info/stocks since /v1/v2/v3 stocks-by-warehouse
        endpoints are either deprecated or undocumented.
        """
        print("\n🧪 Test 3: Stocks (v3/product/info/stocks)")
        result = await self._post("/v3/product/info/stocks", {
            "page": 1,
            "page_size": 50,
        })

        if result.get("error"):
            print(f"   ❌ FAILED: {result.get('message')}")
            self.results["stocks"] = result
            return

        items = result.get("items", [])
        print(f"   ✅ OK! Found {len(items)} stock records")

        for item in items[:5]:
            sku = item.get("offer_id", "N/A")
            stocks = item.get("stocks", [])
            total_qty = sum(s.get("present", 0) for s in stocks)
            print(f"   • SKU: {sku} | Total Qty: {total_qty} | Warehouses: {len(stocks)}")

        self.results["stocks"] = {
            "count": len(items),
            "sample": items[:5],
        }

    async def test_orders_fbo(self):
        """Test 4: Get FBO orders."""
        print("\n🧪 Test 4: FBO Orders (v2/posting/fbo/list)")

        date_to = datetime.now(timezone.utc)
        date_from = date_to - timedelta(days=7)

        result = await self._post("/v2/posting/fbo/list", {
            "dir": "ASC",
            "filter": {
                "since": date_from.isoformat().replace("+00:00", "Z"),
                "to": date_to.isoformat().replace("+00:00", "Z"),
            },
            "limit": 50,
            "offset": 0,
            "with": {"analytics_data": True},
        })

        if result.get("error"):
            print(f"   ❌ FAILED: {result.get('message')}")
            self.results["orders_fbo"] = result
            return

        orders = result.get("result", [])
        print(f"   ✅ OK! Found {len(orders)} FBO orders")

        for order in orders[:3]:
            products = order.get("products", [])
            total = sum(float(p.get("price", 0) or 0) * p.get("quantity", 1) for p in products)
            print(f"   • Order {order.get('posting_number', 'N/A')} | {len(products)} items | {total:.2f}₽ | Status: {order.get('status')}")

        self.results["orders_fbo"] = {
            "count": len(orders),
            "sample": orders[:3],
        }

    async def test_orders_fbs(self):
        """Test 5: Get FBS orders."""
        print("\n🧪 Test 5: FBS Orders (v3/posting/fbs/list)")

        date_to = datetime.now(timezone.utc)
        date_from = date_to - timedelta(days=7)

        result = await self._post("/v3/posting/fbs/list", {
            "dir": "ASC",
            "filter": {
                "since": date_from.isoformat().replace("+00:00", "Z"),
                "to": date_to.isoformat().replace("+00:00", "Z"),
            },
            "limit": 50,
            "offset": 0,
            "with": {"analytics_data": True},
        })

        if result.get("error"):
            print(f"   ❌ FAILED: {result.get('message')}")
            self.results["orders_fbs"] = result
            return

        orders = result.get("result", {}).get("postings", [])
        print(f"   ✅ OK! Found {len(orders)} FBS orders")

        for order in orders[:3]:
            products = order.get("products", [])
            total = sum(float(p.get("price", 0) or 0) * p.get("quantity", 1) for p in products)
            print(f"   • Order {order.get('posting_number', 'N/A')} | {len(products)} items | {total:.2f}₽ | Status: {order.get('status')}")

        self.results["orders_fbs"] = {
            "count": len(orders),
            "sample": orders[:3],
        }

    async def test_analytics(self):
        """Test 6: Get analytics data."""
        print("\n🧪 Test 6: Analytics (v1/analytics/data)")

        date_to = datetime.now(timezone.utc)
        date_from = date_to - timedelta(days=7)

        result = await self._post("/v1/analytics/data", {
            "date_from": date_from.strftime("%Y-%m-%d"),
            "date_to": date_to.strftime("%Y-%m-%d"),
            "metrics": ["ordered_units", "revenue", "cancelled_units"],
            "dimension": ["sku"],
            "filters": [],
            "sort": [{"key": "revenue", "order": "DESC"}],
            "limit": 20,
        })

        if result.get("error"):
            print(f"   ❌ FAILED: {result.get('message')}")
            self.results["analytics"] = result
            return

        data = result.get("data", [])
        print(f"   ✅ OK! Found {len(data)} analytics rows")

        for row in data[:5]:
            dims = row.get("dimensions", [{}])[0]
            metrics = row.get("metrics", [0, 0, 0])
            sku = dims.get("sku", "N/A")
            ordered = metrics[0] if len(metrics) > 0 else 0
            revenue = metrics[1] if len(metrics) > 1 else 0
            print(f"   • SKU: {sku} | Ordered: {ordered} | Revenue: {revenue}₽")

        self.results["analytics"] = {
            "count": len(data),
            "sample": data[:5],
        }

    async def test_campaigns(self):
        """Test 7: Get advert campaigns.

        SKIPPED: Ozon Seller API (api-seller.ozon.ru) does not provide
        advertising campaign endpoints. Ad data requires Ozon Performance API
        (api-performance.ozon.ru) with OAuth authentication.
        """
        print("\n🧪 Test 7: Ad Campaigns")
        print("   ⏭️  SKIPPED: Ozon ads require Performance API (OAuth)")
        print("   Seller API (api-seller.ozon.ru) has no /v1/campaign/list endpoint")

        self.results["campaigns"] = {
            "skipped": True,
            "reason": "Ozon Performance API required (OAuth, separate from Seller API)",
        }

    async def run_all_tests(self):
        """Run all tests with rate limiting delays."""
        print("=" * 60)
        print("Ozon Seller API Test")
        print("=" * 60)
        print(f"Client ID: {self.client_id[:4]}...{self.client_id[-4:]}")
        print(f"API Key: {self.api_key[:4]}...{self.api_key[-4:]}")

        # Test auth first
        if not await self.test_auth():
            print("\n❌ Authentication failed. Check your credentials.")
            return False

        # Rate limit: 1 second delay between requests
        await asyncio.sleep(1)
        await self.test_products()

        await asyncio.sleep(1)
        await self.test_stocks()

        await asyncio.sleep(1)
        await self.test_orders_fbo()

        await asyncio.sleep(1)
        await self.test_orders_fbs()

        await asyncio.sleep(1)
        await self.test_analytics()

        await asyncio.sleep(1)
        await self.test_campaigns()

        # Save results
        filename = f"ozon_test_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(self.results, f, ensure_ascii=False, indent=2, default=str)

        print(f"\n✅ All tests completed!")
        print(f"📄 Results saved to: {filename}")
        return True


def main():
    parser = argparse.ArgumentParser(description="Test Ozon Seller API")
    parser.add_argument("--client-id", required=True, help="Your Ozon Client ID")
    parser.add_argument("--api-key", required=True, help="Your Ozon API Key")
    args = parser.parse_args()

    tester = OzonTester(args.client_id, args.api_key)
    asyncio.run(tester.run_all_tests())


if __name__ == "__main__":
    main()
