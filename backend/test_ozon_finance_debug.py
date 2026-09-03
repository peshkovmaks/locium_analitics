#!/usr/bin/env python3
"""Debug Ozon finance report 400 response."""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
import httpx

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


async def main():
    client_id = os.getenv("OZON_CLIENT_ID", "")
    api_key = os.getenv("OZON_API_KEY", "")
    if not client_id or not api_key:
        print("Missing credentials")
        return

    headers = {
        "Client-Id": client_id,
        "Api-Key": api_key,
        "Content-Type": "application/json",
    }

    date_to = datetime.now(timezone.utc)
    date_from = date_to - timedelta(days=7)

    payload = {
        "filter": {
            "date": {
                "from": date_from.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
                "to": date_to.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            },
            "operation_type": [],
            "posting_number": "",
            "transaction_type": "all",
        },
        "page": 1,
        "page_size": 1000,
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api-seller.ozon.ru/v3/finance/transaction/list",
            headers=headers,
            json=payload,
            timeout=60.0,
        )
        print(f"Status: {resp.status_code}")
        print(f"Body: {resp.text[:1000]}")


if __name__ == "__main__":
    asyncio.run(main())
