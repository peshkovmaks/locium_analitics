#!/usr/bin/env python3
"""Test OzonAdapter.get_finance_report directly for a given range."""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.adapters.ozon import OzonAdapter


async def main(days: int):
    adapter = OzonAdapter(
        shop_id="test",
        credentials={
            "client_id": os.getenv("OZON_CLIENT_ID", ""),
            "api_key": os.getenv("OZON_API_KEY", ""),
        },
    )
    date_to = datetime.now(timezone.utc)
    date_from = date_to - timedelta(days=days)
    try:
        rows = await adapter.get_finance_report(date_from, date_to)
        print(f"{days} days: OK {len(rows)} rows")
    except Exception as e:
        print(f"{days} days: ERROR {e}")
        if hasattr(e, "response"):
            print(f"Response: {e.response.text[:500]}")


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    asyncio.run(main(days))
