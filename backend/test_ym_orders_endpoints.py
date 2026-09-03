#!/usr/bin/env python3
"""Test Yandex Market endpoints for historical orders."""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import httpx

BASE_URL = "https://api.partner.market.yandex.ru"
API_KEY = os.getenv("YM_API_KEY", "").strip()
CAMPAIGN_ID = os.getenv("YM_CAMPAIGN_ID", "").strip()
HEADERS = {"Api-Key": API_KEY, "Content-Type": "application/json"}


def _fmt_date(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


async def _post(client, endpoint, data):
    resp = await client.post(f"{BASE_URL}{endpoint}", headers=HEADERS, json=data, timeout=30.0)
    try:
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError:
        return {"error": resp.status_code, "text": resp.text[:300]}


async def _get(client, endpoint, params=None):
    resp = await client.get(f"{BASE_URL}{endpoint}", headers=HEADERS, params=params, timeout=30.0)
    try:
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError:
        return {"error": resp.status_code, "text": resp.text[:300]}


async def main():
    if not API_KEY or not CAMPAIGN_ID:
        print("Missing credentials")
        return

    date_to = datetime.now(timezone.utc)
    date_from = date_to - timedelta(days=30)

    async with httpx.AsyncClient() as client:
        print("1. POST /v2/campaigns/{campaignId}/stats/orders (current adapter)")
        r = await _post(
            client,
            f"/v2/campaigns/{CAMPAIGN_ID}/stats/orders",
            {"dateFrom": _fmt_date(date_from), "dateTo": _fmt_date(date_to), "limit": 200},
        )
        if "error" in r:
            print(f"  error {r['error']}: {r.get('text')}")
        else:
            orders = r.get("result", {}).get("orders", [])
            print(f"  count={len(orders)}")
            if orders:
                print(f"  first date={orders[0].get('creationDate')}, last={orders[-1].get('creationDate')}")

        await asyncio.sleep(0.5)

        print("2. GET /v2/campaigns/{campaignId}/orders")
        r = await _get(
            client,
            f"/v2/campaigns/{CAMPAIGN_ID}/orders",
            {"fromDate": _fmt_date(date_from), "toDate": _fmt_date(date_to), "limit": 50},
        )
        if "error" in r:
            print(f"  error {r['error']}: {r.get('text')}")
        else:
            orders = r if isinstance(r, list) else r.get("orders", [])
            print(f"  count={len(orders)}")
            if orders:
                print(f"  first keys={list(orders[0].keys())[:10]}")

        await asyncio.sleep(0.5)

        print("3. POST /v2/campaigns/{campaignId}/orders (if GET not supported)")
        r = await _post(
            client,
            f"/v2/campaigns/{CAMPAIGN_ID}/orders",
            {"fromDate": _fmt_date(date_from), "toDate": _fmt_date(date_to), "limit": 50},
        )
        if "error" in r:
            print(f"  error {r['error']}: {r.get('text')}")
        else:
            orders = r if isinstance(r, list) else r.get("orders", [])
            print(f"  count={len(orders)}")

        print("4. GET /v2/campaigns/{campaignId}/orders/tracking (track numbers)")
        r = await _get(client, f"/v2/campaigns/{CAMPAIGN_ID}/orders/tracking", params={"limit": 10})
        print(f"  status/error: {r.get('error') or 'OK'}")


if __name__ == "__main__":
    asyncio.run(main())
