#!/usr/bin/env python3
"""Test Ozon finance report for various date ranges."""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.adapters.ozon import OzonAdapter


async def test(days: int):
    date_to = datetime.now(timezone.utc)
    date_from = date_to - timedelta(days=days)
    adapter = OzonAdapter(
        shop_id="test",
        credentials={
            "client_id": os.getenv("OZON_CLIENT_ID", ""),
            "api_key": os.getenv("OZON_API_KEY", ""),
        },
    )
    try:
        rows = await adapter.get_finance_report(date_from, date_to)
        print(f"{days} days: OK, {len(rows)} rows")
    except Exception as e:
        print(f"{days} days: ERROR {e}")


async def main():
    for days in [30, 90, 120, 180, 365]:
        await test(days)


if __name__ == "__main__":
    asyncio.run(main())
