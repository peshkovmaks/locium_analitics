#!/usr/bin/env python3
"""Focused test of Yandex Market expense endpoints.

Loads credentials from backend/.env, makes a minimal number of API calls,
and prints status + response shape for each candidate endpoint.
"""
import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api.partner.market.yandex.ru"
API_KEY = os.getenv("YM_API_KEY", "").strip()
CAMPAIGN_ID = os.getenv("YM_CAMPAIGN_ID", "").strip()
BUSINESS_ID = os.getenv("YM_BUSINESS_ID", "").strip()

HEADERS = {
    "Api-Key": API_KEY,
    "Content-Type": "application/json",
}

DATE_TO = datetime.now(timezone.utc)
DATE_FROM = DATE_TO - timedelta(days=7)


def _status(resp: httpx.Response) -> str:
    return f"{resp.status_code} {resp.reason_phrase}"


async def _get(client: httpx.AsyncClient, endpoint: str, params: dict = None):
    resp = await client.get(f"{BASE_URL}{endpoint}", params=params, timeout=30.0)
    try:
        resp.raise_for_status()
        return {"status": _status(resp), "data": resp.json()}
    except httpx.HTTPStatusError:
        return {"status": _status(resp), "text": resp.text[:500]}


async def _post(client: httpx.AsyncClient, endpoint: str, data: dict = None):
    resp = await client.post(f"{BASE_URL}{endpoint}", json=data or {}, timeout=30.0)
    try:
        resp.raise_for_status()
        return {"status": _status(resp), "data": resp.json()}
    except httpx.HTTPStatusError:
        return {"status": _status(resp), "text": resp.text[:500]}


def _structure(data, max_depth=2, current_depth=0):
    """Return a compact structural summary of nested dicts/lists."""
    if current_depth >= max_depth:
        return "..."
    if isinstance(data, dict):
        return {k: _structure(v, max_depth, current_depth + 1) for k, v in list(data.items())[:15]}
    if isinstance(data, list):
        n = len(data)
        if n == 0:
            return []
        return [f"list({n})", _structure(data[0], max_depth, current_depth + 1)]
    return type(data).__name__


def _print_result(name: str, result: dict):
    print(f"\n{name}")
    print(f"  status: {result['status']}")
    if "data" in result:
        print(f"  structure: {json.dumps(_structure(result['data']), ensure_ascii=False)}")
    else:
        print(f"  response: {result.get('text')}")


async def main():
    if not API_KEY:
        print("YM_API_KEY not set in .env")
        return

    async with httpx.AsyncClient(headers=HEADERS) as client:
        # 1. Campaigns (and auto-detect campaign id if missing)
        campaigns = await _get(client, "/v2/campaigns", params={"limit": 10})
        _print_result("1. GET /v2/campaigns", campaigns)

        campaign_id = CAMPAIGN_ID
        if not campaign_id and campaigns.get("data"):
            campaigns_list = campaigns["data"] if isinstance(campaigns["data"], list) else campaigns["data"].get("campaigns", [])
            if campaigns_list:
                campaign_id = str(campaigns_list[0].get("id", ""))
                print(f"  -> auto-selected campaign_id: {campaign_id}")

        if not campaign_id:
            print("No campaign_id available, stopping.")
            return

        # 2. Order stats — check whether payments/commissions are populated
        orders = await _post(
            client,
            f"/v2/campaigns/{campaign_id}/stats/orders",
            {
                "dateFrom": DATE_FROM.strftime("%Y-%m-%d"),
                "dateTo": DATE_TO.strftime("%Y-%m-%d"),
                "limit": 5,
            },
        )
        _print_result("2. POST /v2/campaigns/{campaignId}/stats/orders", orders)
        if "data" in orders:
            orders_result = orders["data"].get("result", {})
            sample_orders = orders_result.get("orders", []) if isinstance(orders_result, dict) else []
            if sample_orders:
                sample = sample_orders[0]
                print(f"  -> sample order keys: {list(sample.keys())}")
                print(f"  -> payments: {bool(sample.get('payments'))}, commissions: {bool(sample.get('commissions'))}")

        # 3. United marketplace services report (accrual date range)
        services_range = await _post(
            client,
            "/v2/reports/united-marketplace-services/generate",
            {
                "businessId": int(BUSINESS_ID) if BUSINESS_ID.isdigit() else 0,
                "dateFrom": DATE_FROM.strftime("%Y-%m-%d"),
                "dateTo": DATE_TO.strftime("%Y-%m-%d"),
            },
        )
        _print_result("3. POST /v2/reports/united-marketplace-services/generate (date range)", services_range)
        await asyncio.sleep(1)

        # 4. United marketplace services report (year/month)
        services_month = await _post(
            client,
            "/v2/reports/united-marketplace-services/generate",
            {
                "businessId": int(BUSINESS_ID) if BUSINESS_ID.isdigit() else 0,
                "year": DATE_TO.year,
                "month": DATE_TO.month,
            },
        )
        _print_result("4. POST /v2/reports/united-marketplace-services/generate (year/month)", services_month)
        await asyncio.sleep(1)

        # 5. United netting report
        netting = await _post(
            client,
            "/v2/reports/united-netting/generate",
            {
                "businessId": int(BUSINESS_ID) if BUSINESS_ID.isdigit() else 0,
                "dateFrom": DATE_FROM.strftime("%Y-%m-%d"),
                "dateTo": DATE_TO.strftime("%Y-%m-%d"),
            },
        )
        _print_result("5. POST /v2/reports/united-netting/generate", netting)
        await asyncio.sleep(1)

        # 6. Campaign services
        campaign_services = await _get(client, f"/v2/campaigns/{campaign_id}/services")
        _print_result("6. GET /v2/campaigns/{campaignId}/services", campaign_services)

        # 7. Business services (if business id available)
        if BUSINESS_ID:
            business_services = await _get(client, f"/v2/businesses/{BUSINESS_ID}/services")
            _print_result("7. GET /v2/businesses/{businessId}/services", business_services)


if __name__ == "__main__":
    asyncio.run(main())
