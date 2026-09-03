#!/usr/bin/env python3
"""Test fallback Yandex Market endpoints for expense details.

Tests additional candidate endpoints not covered by test_ym_expense_endpoints.py:
- /v2/reports/united-netting/generate
- /v2/reports/info/list
- /v2/campaigns/{campaignId}/services
- /v2/businesses/{businessId}/services
- /v2/campaigns/{campaignId}/orders/{orderId}
- /v2/campaigns/{campaignId}/orders/{orderId}/items
- /v2/reports/goods-realization/generate with campaignId/month/year

Results appended to ym_expense_endpoint_results.json under "fallback_endpoints".
"""

import asyncio
import json
import logging
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.adapters.yandex_market import YandexMarketAdapter
from app.database import get_async_session_maker
from app.models import Shop
from app.utils.encryption import decrypt_dict

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("ym_expense_fallbacks")

SHOP_ID = "b87e8b29-f280-46f2-b25d-9055e9325401"
DATE_FROM = "2026-08-01"
DATE_TO = "2026-08-24"
RESULT_FILE = Path(__file__).resolve().parent / "ym_expense_endpoint_results.json"


def now_utc():
    return datetime.now(timezone.utc).isoformat()


async def load_credentials():
    async with get_async_session_maker()() as session:
        stmt = select(Shop).where(Shop.id == uuid.UUID(SHOP_ID))
        result = await session.execute(stmt)
        shop = result.scalar_one_or_none()
        if shop is None:
            raise ValueError(f"Shop {SHOP_ID} not found")
        creds = decrypt_dict(shop.credentials)
        return creds


def make_adapter(credentials):
    return YandexMarketAdapter(SHOP_ID, credentials)


def describe_structure(body, depth=0, max_depth=3):
    if depth > max_depth:
        return "..."
    if isinstance(body, dict):
        return {k: describe_structure(v, depth + 1, max_depth) for k, v in body.items()}
    if isinstance(body, list):
        if not body:
            return "[]"
        return [describe_structure(body[0], depth + 1, max_depth), f"... ({len(body) - 1} more)"]
    return type(body).__name__


async def raw_request(adapter, method, endpoint, payload=None, params=None):
    url = f"{adapter.BASE_URL}{endpoint}"
    async with httpx.AsyncClient() as client:
        start = time.time()
        try:
            if method == "GET":
                response = await client.get(url, headers=adapter.headers, params=params, timeout=30.0)
            else:
                response = await client.post(url, headers=adapter.headers, json=payload or {}, timeout=30.0)
            response.raise_for_status()
            elapsed = time.time() - start
            body = None
            try:
                body = response.json()
            except Exception:
                body = response.text
            return {
                "status": response.status_code,
                "reason": response.reason_phrase,
                "retry_after": response.headers.get("retry-after"),
                "elapsed_seconds": round(elapsed, 2),
                "body": body,
                "structure": describe_structure(body),
            }
        except httpx.HTTPStatusError as exc:
            elapsed = time.time() - start
            body = None
            try:
                body = exc.response.json()
            except Exception:
                body = exc.response.text
            return {
                "status": exc.response.status_code,
                "reason": exc.response.reason_phrase,
                "retry_after": exc.response.headers.get("retry-after"),
                "elapsed_seconds": round(elapsed, 2),
                "body": body,
                "structure": describe_structure(body),
            }
        except Exception as exc:
            elapsed = time.time() - start
            return {
                "status": None,
                "reason": str(type(exc).__name__),
                "retry_after": None,
                "elapsed_seconds": round(elapsed, 2),
                "body": {"error": str(exc)},
                "structure": describe_structure({"error": str(exc)}),
            }


async def poll_report(adapter, report_id, timeout=180):
    start = time.time()
    attempts = []
    while time.time() - start < timeout:
        await asyncio.sleep(10)
        info = await raw_request(adapter, "GET", f"/v2/reports/info/{report_id}")
        attempts.append({"time": now_utc(), "status": info.get("status"), "structure": info.get("structure")})
        body = info.get("body", {}) or {}
        status = ""
        if isinstance(body, dict):
            status = (body.get("result") or body).get("status", "").upper()
        if status == "DONE":
            return {"status": "DONE", "file_url": (body.get("result") or body).get("file"), "poll_attempts": attempts}
        if status in ("FAILED", "ERROR", "CANCELLED"):
            return {"status": status, "file_url": None, "poll_attempts": attempts}
    return {"status": "TIMEOUT", "file_url": None, "poll_attempts": attempts}


async def get_sample_order_id(adapter, campaign_id):
    """Fetch one recent order ID from stats/orders to test order-scoped endpoints."""
    result = await raw_request(adapter, "POST", f"/v2/campaigns/{campaign_id}/stats/orders", {"dateFrom": DATE_FROM, "dateTo": DATE_TO, "limit": 5})
    body = result.get("body", {})
    if isinstance(body, dict):
        orders = body.get("result", {}).get("orders", [])
        if orders:
            return str(orders[0].get("id"))
    return None


async def main():
    credentials = await load_credentials()
    adapter = make_adapter(credentials)
    business_id = credentials.get("business_id")
    campaign_id = credentials.get("campaign_id")

    results = {
        "shop_id": SHOP_ID,
        "business_id": business_id,
        "campaign_id": campaign_id,
        "date_from": DATE_FROM,
        "date_to": DATE_TO,
        "started_at": now_utc(),
        "fallback_endpoints": [],
    }

    # 1. united-netting
    logger.info("[F1] POST /v2/reports/united-netting/generate")
    r = await raw_request(adapter, "POST", "/v2/reports/united-netting/generate", {"businessId": int(business_id), "dateFrom": DATE_FROM, "dateTo": DATE_TO})
    report_id = None
    if isinstance(r.get("body"), dict):
        report_id = r["body"].get("result", {}).get("reportId") or r["body"].get("reportId")
    if report_id:
        r["poll"] = await poll_report(adapter, report_id)
    r["endpoint"] = "/v2/reports/united-netting/generate"
    r["request_payload"] = {"businessId": int(business_id), "dateFrom": DATE_FROM, "dateTo": DATE_TO}
    r["summary"] = f"HTTP {r.get('status')} {r.get('reason')}; reportId={report_id}; final={r.get('poll', {}).get('status') if report_id else 'N/A'}"
    results["fallback_endpoints"].append(r)
    logger.info("[F1] %s", r["summary"])
    await asyncio.sleep(10)

    # 2. reports info list
    logger.info("[F2] GET /v2/reports/info/list")
    r = await raw_request(adapter, "GET", "/v2/reports/info/list", params={"limit": 20})
    r["endpoint"] = "/v2/reports/info/list"
    r["summary"] = f"HTTP {r.get('status')} {r.get('reason')}"
    results["fallback_endpoints"].append(r)
    logger.info("[F2] %s", r["summary"])
    await asyncio.sleep(5)

    # 3. campaigns services (plausible path)
    logger.info("[F3] POST /v2/campaigns/{campaignId}/services")
    r = await raw_request(adapter, "POST", f"/v2/campaigns/{campaign_id}/services", {"dateFrom": DATE_FROM, "dateTo": DATE_TO})
    r["endpoint"] = f"/v2/campaigns/{campaign_id}/services"
    r["request_payload"] = {"dateFrom": DATE_FROM, "dateTo": DATE_TO}
    r["summary"] = f"HTTP {r.get('status')} {r.get('reason')}"
    results["fallback_endpoints"].append(r)
    logger.info("[F3] %s", r["summary"])
    await asyncio.sleep(5)

    # 4. businesses services
    logger.info("[F4] POST /v2/businesses/{businessId}/services")
    r = await raw_request(adapter, "POST", f"/v2/businesses/{business_id}/services", {"dateFrom": DATE_FROM, "dateTo": DATE_TO})
    r["endpoint"] = f"/v2/businesses/{business_id}/services"
    r["request_payload"] = {"dateFrom": DATE_FROM, "dateTo": DATE_TO}
    r["summary"] = f"HTTP {r.get('status')} {r.get('reason')}"
    results["fallback_endpoints"].append(r)
    logger.info("[F4] %s", r["summary"])
    await asyncio.sleep(5)

    # 5. order details and items
    sample_order_id = await get_sample_order_id(adapter, campaign_id)
    if sample_order_id:
        logger.info("[F5] GET /v2/campaigns/{campaignId}/orders/{orderId} (orderId=%s)", sample_order_id)
        r = await raw_request(adapter, "GET", f"/v2/campaigns/{campaign_id}/orders/{sample_order_id}")
        r["endpoint"] = f"/v2/campaigns/{campaign_id}/orders/{sample_order_id}"
        r["summary"] = f"HTTP {r.get('status')} {r.get('reason')}"
        results["fallback_endpoints"].append(r)
        logger.info("[F5] %s", r["summary"])
        await asyncio.sleep(5)

        logger.info("[F6] GET /v2/campaigns/{campaignId}/orders/{orderId}/items")
        r = await raw_request(adapter, "GET", f"/v2/campaigns/{campaign_id}/orders/{sample_order_id}/items")
        r["endpoint"] = f"/v2/campaigns/{campaign_id}/orders/{sample_order_id}/items"
        r["summary"] = f"HTTP {r.get('status')} {r.get('reason')}"
        results["fallback_endpoints"].append(r)
        logger.info("[F6] %s", r["summary"])
        await asyncio.sleep(5)
    else:
        results["fallback_endpoints"].append({"endpoint": "order-scoped", "summary": "No sample order found; skipped"})

    # 7. goods-realization with campaign/month/year
    logger.info("[F7] POST /v2/reports/goods-realization/generate (with campaignId, month, year)")
    r = await raw_request(adapter, "POST", "/v2/reports/goods-realization/generate", {"businessId": int(business_id), "campaignId": int(campaign_id), "year": 2026, "month": 8})
    report_id = None
    if isinstance(r.get("body"), dict):
        report_id = r["body"].get("result", {}).get("reportId") or r["body"].get("reportId")
    if report_id:
        r["poll"] = await poll_report(adapter, report_id)
    r["endpoint"] = "/v2/reports/goods-realization/generate"
    r["request_payload"] = {"businessId": int(business_id), "campaignId": int(campaign_id), "year": 2026, "month": 8}
    r["summary"] = f"HTTP {r.get('status')} {r.get('reason')}; reportId={report_id}; final={r.get('poll', {}).get('status') if report_id else 'N/A'}"
    results["fallback_endpoints"].append(r)
    logger.info("[F7] %s", r["summary"])

    # 8. bids/info (advertising bids)
    logger.info("[F8] POST /v2/businesses/{businessId}/bids/info")
    r = await raw_request(adapter, "POST", f"/v2/businesses/{business_id}/bids/info", {})
    r["endpoint"] = f"/v2/businesses/{business_id}/bids/info"
    r["summary"] = f"HTTP {r.get('status')} {r.get('reason')}"
    results["fallback_endpoints"].append(r)
    logger.info("[F8] %s", r["summary"])

    results["finished_at"] = now_utc()

    # Merge with existing results file if present
    existing = {}
    if RESULT_FILE.exists():
        with open(RESULT_FILE, "r", encoding="utf-8") as f:
            existing = json.load(f)
    existing["fallback_endpoints"] = results["fallback_endpoints"]
    existing["fallback_started_at"] = results["started_at"]
    existing["fallback_finished_at"] = results["finished_at"]

    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2, default=str)

    logger.info("Results merged into %s", RESULT_FILE)

    print("\n===== FALLBACK SUMMARY =====")
    for entry in results["fallback_endpoints"]:
        print(f"\n{entry['endpoint']}")
        print(f"  Status: {entry.get('status')} {entry.get('reason', '')}")
        print(f"  Retry-After: {entry.get('retry_after')}")
        print(f"  Structure: {json.dumps(entry.get('structure'), ensure_ascii=False)}")
        print(f"  {entry['summary']}")
    print(f"\nFull results: {RESULT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
