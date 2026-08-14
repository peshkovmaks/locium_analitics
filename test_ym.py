#!/usr/bin/env python3
"""
Standalone test script for Yandex Market Partner API.

Supports TWO auth methods:
  1. Api-Key (recommended by Yandex Market, unlimited validity)
  2. OAuth token (legacy, 1 year validity)

Usage with Api-Key:
    python test_ym.py --api-key YOUR_API_KEY --campaign-id 90279888

Usage with OAuth (legacy):
    python test_ym.py --oauth-token YOUR_TOKEN --business-id 5975487 --campaign-id 90279888

What it tests:
1. Authentication (GET /v2/campaigns)
2. Order stats (POST /v2/campaigns/{campaignId}/stats/orders)
3. Stocks (POST /v2/campaigns/{campaignId}/offers/stocks)
4. Prices (GET /v2/campaigns/{campaignId}/offer-prices)
5. Bids/Adverts (POST /v2/businesses/{businessId}/bids/info)
6. Finance report generation (POST /v2/reports/united-marketplace-services/generate)

Output: saves results to ym_test_results.json
"""

import argparse
import json
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import httpx


class YandexMarketTester:
    BASE_URL = "https://api.partner.market.yandex.ru"

    def __init__(self, api_key: str = None, oauth_token: str = None, business_id: str = None, campaign_id: str = None):
        self.api_key = api_key
        self.oauth_token = oauth_token
        self.business_id = business_id
        self.campaign_id = campaign_id
        self.results = {}
        
        # Build headers based on auth method
        if api_key:
            self.headers = {
                "Api-Key": api_key,
                "Content-Type": "application/json",
            }
            self.auth_method = "api_key"
        elif oauth_token:
            self.headers = {
                "Authorization": f"Bearer {oauth_token}",
                "Content-Type": "application/json",
            }
            if business_id:
                self.headers["X-Business-Id"] = business_id
            self.auth_method = "oauth"
        else:
            raise ValueError("Either --api-key or --oauth-token must be provided")

    async def _get(self, endpoint: str, params: dict = None) -> dict:
        url = f"{self.BASE_URL}{endpoint}"
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

    async def _post(self, endpoint: str, data: dict = None) -> dict:
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
        print("\n🧪 Test 1: Authentication (GET /v2/campaigns)")
        print(f"   Auth method: {self.auth_method.upper()}")
        result = await self._get("/v2/campaigns", params={"limit": 10})

        if result.get("error"):
            print(f"   ❌ FAILED: {result.get('status_code')} - {result.get('message')}")
            self.results["auth"] = result
            return False

        campaigns = result if isinstance(result, list) else result.get("campaigns", [])
        print(f"   ✅ OK! Found {len(campaigns)} campaigns (stores)")
        
        for camp in campaigns[:3]:
            print(f"   • {camp.get('domain', 'Unknown')} (ID: {camp.get('id')}) | Type: {camp.get('placementType')}")
        
        # Extract business_id from first campaign if not provided
        if campaigns and not self.business_id:
            first_business = campaigns[0].get("business", {})
            extracted_bid = first_business.get("id")
            if extracted_bid:
                self.business_id = str(extracted_bid)
                print(f"   📌 Auto-detected Business ID: {self.business_id}")
        
        self.results["auth"] = {"success": True, "campaigns_count": len(campaigns), "campaigns": campaigns[:3]}
        return True

    async def test_orders(self):
        print("\n🧪 Test 2: Order Stats (POST /v2/campaigns/{campaignId}/stats/orders)")
        date_to = datetime.now(timezone.utc)
        date_from = date_to - timedelta(days=7)

        result = await self._post(
            f"/v2/campaigns/{self.campaign_id}/stats/orders",
            {
                "dateFrom": date_from.strftime("%Y-%m-%d"),
                "dateTo": date_to.strftime("%Y-%m-%d"),
                "limit": 200,
            },
        )

        if result.get("error"):
            print(f"   ❌ FAILED: {result.get('message')}")
            self.results["orders"] = result
            return

        orders = result.get("result", {}).get("orders", [])
        print(f"   ✅ OK! Found {len(orders)} orders")

        for order in orders[:3]:
            items = order.get("items", [])
            total = sum(float(i.get("buyerPrice", 0) or 0) * i.get("count", 1) for i in items)
            print(f"   • Order {order.get('id', 'N/A')} | {len(items)} items | {total:.2f}₽ | Status: {order.get('status')}")

        self.results["orders"] = {"count": len(orders), "sample": orders[:3]}

    async def test_stocks(self):
        print("\n🧪 Test 3: Stocks (POST /v2/campaigns/{campaignId}/offers/stocks)")
        result = await self._post(
            f"/v2/campaigns/{self.campaign_id}/offers/stocks",
            {"limit": 200},
        )

        if result.get("error"):
            print(f"   ❌ FAILED: {result.get('message')}")
            self.results["stocks"] = result
            return

        offers = result.get("result", {}).get("offers", []) if isinstance(result, dict) else []
        print(f"   ✅ OK! Found {len(offers)} stock records")

        for offer in offers[:5]:
            sku = offer.get("offerId", "N/A")
            stocks = offer.get("stocks", [])
            total_qty = sum(s.get("count", 0) for s in stocks)
            print(f"   • SKU: {sku} | Total Qty: {total_qty} | Warehouses: {len(stocks)}")

        self.results["stocks"] = {"count": len(offers), "sample": offers[:5]}

    async def test_prices(self):
        print("\n🧪 Test 4: Prices (GET /v2/campaigns/{campaignId}/offer-prices)")
        result = await self._get(f"/v2/campaigns/{self.campaign_id}/offer-prices", params={"limit": 200})

        if result.get("error"):
            print(f"   ❌ FAILED: {result.get('message')}")
            self.results["prices"] = result
            return

        prices_list = result.get("result", {}).get("offers", []) if isinstance(result, dict) else []
        print(f"   ✅ OK! Found {len(prices_list)} price records")

        for item in prices_list[:5]:
            sku = item.get("offerId", "N/A")
            price = item.get("price", {}).get("value", 0) if isinstance(item.get("price"), dict) else 0
            print(f"   • SKU: {sku} | Price: {price}₽")

        self.results["prices"] = {"count": len(prices_list), "sample": prices_list[:5]}

    async def test_bids(self):
        if not self.business_id:
            print("\n🧪 Test 5: Bids/Adverts")
            print("   ⏭️  SKIPPED: Business ID not available")
            self.results["bids"] = {"skipped": True, "reason": "Business ID required"}
            return

        print("\n🧪 Test 5: Bids/Adverts (POST /v2/businesses/{businessId}/bids/info)")
        result = await self._post(
            f"/v2/businesses/{self.business_id}/bids/info",
            {},
        )

        if result.get("error"):
            print(f"   ❌ FAILED: {result.get('message')}")
            self.results["bids"] = result
            return

        offers = result.get("result", {}).get("offers", [])
        print(f"   ✅ OK! Found {len(offers)} bid entries")

        for offer in offers[:5]:
            print(f"   • SKU: {offer.get('offerId')} | Bid: {offer.get('bid', 0)}₽")

        self.results["bids"] = {"count": len(offers), "sample": offers[:5]}

    async def test_finance(self):
        print("\n🧪 Test 6: Finance Report (POST /v2/reports/united-marketplace-services/generate)")
        date_to = datetime.now(timezone.utc)
        date_from = date_to - timedelta(days=7)

        result = await self._post(
            "/v2/reports/united-marketplace-services/generate",
            {
                "businessId": int(self.business_id) if self.business_id and self.business_id.isdigit() else 0,
                "dateFrom": date_from.strftime("%Y-%m-%d"),
                "dateTo": date_to.strftime("%Y-%m-%d"),
            },
        )

        if result.get("error"):
            print(f"   ❌ FAILED: {result.get('message')}")
            self.results["finance"] = result
            return

        report_id = result.get("result", {}).get("reportId")
        print(f"   ✅ OK! Report generation started")
        print(f"   • Report ID: {report_id}")
        print(f"   ⚠️  Note: Report is async. Poll with GET /v2/reports/info/{report_id} to download.")

        self.results["finance"] = {"report_id": report_id, "status": "generating"}

    async def run_all_tests(self):
        print("=" * 60)
        print("Yandex Market Partner API Test")
        print("=" * 60)
        print(f"Campaign ID: {self.campaign_id}")
        if self.business_id:
            print(f"Business ID: {self.business_id}")
        print(f"Auth: {self.auth_method.upper()}")

        if not await self.test_auth():
            print("\n❌ Authentication failed.")
            print("   💡 TIP: Yandex Market now recommends Api-Key instead of OAuth.")
            print("      Get Api-Key in: ЛК Яндекс Маркет → Настройки → API → Создать ключ")
            return False

        await asyncio.sleep(1)
        await self.test_orders()
        await asyncio.sleep(1)
        await self.test_stocks()
        await asyncio.sleep(1)
        await self.test_prices()
        await asyncio.sleep(1)
        await self.test_bids()
        await asyncio.sleep(1)
        await self.test_finance()

        filename = f"ym_test_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(self.results, f, ensure_ascii=False, indent=2, default=str)

        print(f"\n✅ All tests completed!")
        print(f"📄 Results saved to: {filename}")
        return True


def main():
    parser = argparse.ArgumentParser(description="Test Yandex Market Partner API")
    parser.add_argument("--api-key", help="Your YM Api-Key (RECOMMENDED)")
    parser.add_argument("--oauth-token", help="Your YM OAuth token (legacy)")
    parser.add_argument("--business-id", help="Your YM Business ID")
    parser.add_argument("--campaign-id", required=True, help="Your YM Campaign ID")
    args = parser.parse_args()

    if not args.api_key and not args.oauth_token:
        parser.error("Either --api-key or --oauth-token is required")

    tester = YandexMarketTester(
        api_key=args.api_key,
        oauth_token=args.oauth_token,
        business_id=args.business_id,
        campaign_id=args.campaign_id,
    )
    asyncio.run(tester.run_all_tests())


if __name__ == "__main__":
    main()