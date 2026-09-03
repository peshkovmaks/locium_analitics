#!/usr/bin/env python3
"""Temporary probe of Yandex Market finance/expense endpoints.

This script decrypts the credentials for a single shop, creates a
YandexMarketAdapter, then calls five candidate endpoints one at a time
with generous delays to avoid rate limits.  Results are written to
backend/scripts/ym_expense_endpoint_results.json.

Run from the backend directory:
    .venv/bin/python scripts/test_ym_expense_endpoints.py
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
logger = logging.getLogger("ym_expense_probe")

SHOP_ID = "b87e8b29-f280-46f2-b25d-9055e9325401"
DATE_FROM = "2026-08-01"
DATE_TO = "2026-08-24"
RESULT_FILE = Path(__file__).resolve().parent / "ym_expense_endpoint_results.json"


async def load_credentials():
    async with get_async_session_maker()() as session:
        stmt = select(Shop).where(Shop.id == uuid.UUID(SHOP_ID))
        result = await session.execute(stmt)
        shop = result.scalar_one_or_none()
        if shop is None:
            raise ValueError(f"Shop {SHOP_ID} not found")
        creds = decrypt_dict(shop.credentials)
        logger.info("Loaded shop %s (%s) credentials keys: %s", shop.name, shop.marketplace.value, list(creds.keys()))
        return creds


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def make_adapter(credentials):
    return YandexMarketAdapter(SHOP_ID, credentials)


def shorten(value, max_len=1200):
    text = json.dumps(value, ensure_ascii=False, default=str) if not isinstance(value, str) else value
    if len(text) <= max_len:
        return value
    return text[:max_len] + f"\n... ({len(text) - max_len} chars truncated)"


async def raw_request(adapter, method, endpoint, payload=None):
    """Make a request, capture status/headers/body without raising."""
    url = f"{adapter.BASE_URL}{endpoint}"
    async with httpx.AsyncClient() as client:
        start = time.time()
        try:
            if method == "GET":
                response = await client.get(url, headers=adapter.headers, timeout=60.0)
            else:
                response = await client.post(url, headers=adapter.headers, json=payload or {}, timeout=60.0)
            elapsed = time.time() - start
            body = None
            content_type = response.headers.get("content-type", "")
            try:
                body = response.json()
            except Exception:
                body = response.text
            return {
                "status": response.status_code,
                "reason": response.reason_phrase,
                "retry_after": response.headers.get("retry-after"),
                "content_type": content_type,
                "elapsed_seconds": round(elapsed, 2),
                "body": body,
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
                "content_type": exc.response.headers.get("content-type", ""),
                "elapsed_seconds": round(elapsed, 2),
                "body": body,
            }
        except Exception as exc:
            elapsed = time.time() - start
            return {
                "status": None,
                "reason": str(type(exc).__name__),
                "retry_after": None,
                "content_type": "",
                "elapsed_seconds": round(elapsed, 2),
                "body": {"error": str(exc)},
            }


def describe_structure(body, depth=0, max_depth=3):
    """Return a compact structural description of a JSON body."""
    if depth > max_depth:
        return "..."
    if isinstance(body, dict):
        return {k: describe_structure(v, depth + 1, max_depth) for k, v in body.items()}
    if isinstance(body, list):
        if not body:
            return "[]"
        return [describe_structure(body[0], depth + 1, max_depth), f"... ({len(body) - 1} more)"]
    return type(body).__name__


def summarize_report_status(result):
    status = result.get("status")
    body = result.get("body", {})
    if status == 200:
        if isinstance(body, dict) and (body.get("result", {}).get("reportId") or body.get("reportId")):
            report_id = body.get("result", {}).get("reportId") or body.get("reportId")
            return f"Report accepted, reportId={report_id}"
        return "HTTP 200 but no reportId"
    if status == 420:
        return "Rate limited (420)"
    if status == 429:
        return "Rate limited (429)"
    if status in (400, 401, 403, 404, 422):
        return f"Client error {status}"
    if status and status >= 500:
        return f"Server error {status}"
    return f"Unexpected status {status}"


async def poll_and_download_report(adapter, report_id, timeout_seconds=180):
    """Poll /v2/reports/info/{reportId} until done or timeout."""
    start = time.time()
    attempts = []
    while time.time() - start < timeout_seconds:
        await asyncio.sleep(10)
        info = await raw_request(adapter, "GET", f"/v2/reports/info/{report_id}")
        attempts.append({"time": now_utc(), "status": info.get("status"), "body": shorten(info.get("body"))})
        body = info.get("body", {}) or {}
        status = ""
        if isinstance(body, dict):
            status = (body.get("result") or body).get("status", "").upper()
        if status == "DONE":
            file_url = (body.get("result") or body).get("file")
            download = None
            if file_url:
                download = await raw_request(adapter, "GET", file_url)
            return {"status": "DONE", "file_url": file_url, "poll_attempts": attempts, "download": download}
        if status in ("FAILED", "ERROR", "CANCELLED"):
            return {"status": status, "file_url": None, "poll_attempts": attempts}
    return {"status": "TIMEOUT", "file_url": None, "poll_attempts": attempts}


async def test_united_marketplace_services(adapter, business_id):
    endpoint = "/v2/reports/united-marketplace-services/generate"
    payload = {
        "businessId": int(business_id),
        "dateFrom": DATE_FROM,
        "dateTo": DATE_TO,
    }
    result = await raw_request(adapter, "POST", endpoint, payload)
    result["endpoint"] = endpoint
    result["request_payload"] = payload
    result["body_short"] = shorten(result["body"])
    result["structure"] = describe_structure(result["body"])

    report_id = None
    if isinstance(result.get("body"), dict):
        report_id = result["body"].get("result", {}).get("reportId") or result["body"].get("reportId")

    if report_id:
        poll = await poll_and_download_report(adapter, report_id)
        result["poll"] = poll

    result["summary"] = summarize_report_status(result)
    if report_id:
        result["summary"] += f"; polled {len(result['poll']['poll_attempts'])} times, final={result['poll']['status']}"

    return result


async def test_united_orders(adapter, business_id):
    endpoint = "/v2/reports/united-orders/generate"
    payload = {
        "businessId": int(business_id),
        "dateFrom": DATE_FROM,
        "dateTo": DATE_TO,
    }
    result = await raw_request(adapter, "POST", endpoint, payload)
    result["endpoint"] = endpoint
    result["request_payload"] = payload
    result["body_short"] = shorten(result["body"])
    result["structure"] = describe_structure(result["body"])

    report_id = None
    if isinstance(result.get("body"), dict):
        report_id = result["body"].get("result", {}).get("reportId") or result["body"].get("reportId")
    if report_id:
        result["poll"] = await poll_and_download_report(adapter, report_id)

    result["summary"] = summarize_report_status(result)
    if report_id:
        result["summary"] += f"; polled {len(result['poll']['poll_attempts'])} times, final={result['poll']['status']}"
    return result


async def test_goods_realization(adapter, business_id):
    endpoint = "/v2/reports/goods-realization/generate"
    payload = {
        "businessId": int(business_id),
        "dateFrom": DATE_FROM,
        "dateTo": DATE_TO,
    }
    result = await raw_request(adapter, "POST", endpoint, payload)
    result["endpoint"] = endpoint
    result["request_payload"] = payload
    result["body_short"] = shorten(result["body"])
    result["structure"] = describe_structure(result["body"])

    report_id = None
    if isinstance(result.get("body"), dict):
        report_id = result["body"].get("result", {}).get("reportId") or result["body"].get("reportId")
    if report_id:
        result["poll"] = await poll_and_download_report(adapter, report_id)

    result["summary"] = summarize_report_status(result)
    if report_id:
        result["summary"] += f"; polled {len(result['poll']['poll_attempts'])} times, final={result['poll']['status']}"
    return result


async def test_marketing_detalization(adapter, business_id):
    endpoint = f"/v1/businesses/{business_id}/reports/marketing-detalization/generate"
    payload = {"dateFrom": DATE_FROM, "dateTo": DATE_TO}
    result = await raw_request(adapter, "POST", endpoint, payload)
    result["endpoint"] = endpoint
    result["request_payload"] = payload
    result["body_short"] = shorten(result["body"])
    result["structure"] = describe_structure(result["body"])

    report_id = None
    if isinstance(result.get("body"), dict):
        report_id = result["body"].get("result", {}).get("reportId") or result["body"].get("reportId")
    if report_id:
        result["poll"] = await poll_and_download_report(adapter, report_id)

    result["summary"] = summarize_report_status(result)
    if report_id:
        result["summary"] += f"; polled {len(result['poll']['poll_attempts'])} times, final={result['poll']['status']}"
    return result


async def test_campaign_stats_orders(adapter, campaign_id):
    endpoint = f"/v2/campaigns/{campaign_id}/stats/orders"
    payload = {"dateFrom": DATE_FROM, "dateTo": DATE_TO, "limit": 200}
    result = await raw_request(adapter, "POST", endpoint, payload)
    result["endpoint"] = endpoint
    result["request_payload"] = payload
    result["body_short"] = shorten(result["body"])
    result["structure"] = describe_structure(result["body"])

    # Analyze whether payments/commissions are populated in the sample
    analysis = {"orders_count": 0, "items_with_payments": 0, "items_with_commissions": 0, "payment_keys": set(), "commission_keys": set()}
    body = result.get("body", {})
    if isinstance(body, dict):
        orders = body.get("result", {}).get("orders", [])
        analysis["orders_count"] = len(orders)
        for order in orders:
            for item in order.get("items", []):
                payments = item.get("payments")
                commissions = item.get("commissions")
                if payments:
                    analysis["items_with_payments"] += 1
                    if isinstance(payments, list) and payments:
                        analysis["payment_keys"].update(payments[0].keys())
                    elif isinstance(payments, dict):
                        analysis["payment_keys"].update(payments.keys())
                if commissions:
                    analysis["items_with_commissions"] += 1
                    if isinstance(commissions, list) and commissions:
                        analysis["commission_keys"].update(commissions[0].keys())
                    elif isinstance(commissions, dict):
                        analysis["commission_keys"].update(commissions.keys())
    result["payments_commissions_analysis"] = {
        **analysis,
        "payment_keys": sorted(analysis["payment_keys"]),
        "commission_keys": sorted(analysis["commission_keys"]),
    }
    result["summary"] = f"HTTP {result.get('status')}; orders={analysis['orders_count']}, items with payments={analysis['items_with_payments']}, items with commissions={analysis['items_with_commissions']}"
    return result


async def main():
    credentials = await load_credentials()
    adapter = make_adapter(credentials)

    business_id = credentials.get("business_id")
    campaign_id = credentials.get("campaign_id")
    if not business_id or not campaign_id:
        raise ValueError("business_id and campaign_id are required in credentials")

    logger.info("Using business_id=%s campaign_id=%s", business_id, campaign_id)

    results = {
        "shop_id": SHOP_ID,
        "business_id": business_id,
        "campaign_id": campaign_id,
        "date_from": DATE_FROM,
        "date_to": DATE_TO,
        "started_at": now_utc(),
        "endpoints": [],
    }

    # Endpoint 1
    logger.info("[1/5] Testing /v2/reports/united-marketplace-services/generate")
    results["endpoints"].append(await test_united_marketplace_services(adapter, business_id))
    logger.info("[1/5] %s", results["endpoints"][-1]["summary"])
    await asyncio.sleep(10)

    # Endpoint 2
    logger.info("[2/5] Testing /v2/reports/united-orders/generate")
    results["endpoints"].append(await test_united_orders(adapter, business_id))
    logger.info("[2/5] %s", results["endpoints"][-1]["summary"])
    await asyncio.sleep(10)

    # Endpoint 3
    logger.info("[3/5] Testing /v2/reports/goods-realization/generate")
    results["endpoints"].append(await test_goods_realization(adapter, business_id))
    logger.info("[3/5] %s", results["endpoints"][-1]["summary"])
    await asyncio.sleep(10)

    # Endpoint 4
    logger.info("[4/5] Testing /v1/businesses/{businessId}/reports/marketing-detalization/generate")
    results["endpoints"].append(await test_marketing_detalization(adapter, business_id))
    logger.info("[4/5] %s", results["endpoints"][-1]["summary"])
    await asyncio.sleep(10)

    # Endpoint 5
    logger.info("[5/5] Testing /v2/campaigns/{campaignId}/stats/orders")
    results["endpoints"].append(await test_campaign_stats_orders(adapter, campaign_id))
    logger.info("[5/5] %s", results["endpoints"][-1]["summary"])

    results["finished_at"] = now_utc()

    with open(RESULT_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)

    logger.info("Results written to %s", RESULT_FILE)

    # Print a concise console summary
    print("\n===== SUMMARY =====")
    for entry in results["endpoints"]:
        print(f"\n{entry['endpoint']}")
        print(f"  Status: {entry['status']} {entry.get('reason', '')}")
        print(f"  Retry-After: {entry.get('retry_after')}")
        print(f"  Structure: {json.dumps(entry.get('structure'), ensure_ascii=False)}")
        print(f"  {entry['summary']}")
    print(f"\nFull results: {RESULT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
