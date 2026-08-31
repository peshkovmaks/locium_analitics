#!/usr/bin/env python3
"""Fetch Yandex Market finance report for a date range and save expenses."""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv('/Users/peshkov/Yandex.Disk.localized/Development/locium_analitics/backend/.env')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.adapters.yandex_market import YandexMarketAdapter
from app.database import get_async_session_maker
from app.models import Shop
from app.services.sync_service import SyncService
from app.utils.encryption import decrypt_dict
from sqlalchemy import select


async def main(days: int = 210):
    session_maker = get_async_session_maker()
    async with session_maker() as db:
        shop = (
            (await db.execute(select(Shop).where(Shop.marketplace == "ym"))).scalars().first()
        )
        if not shop:
            print("YM shop not found")
            return

        credentials = decrypt_dict(dict(shop.credentials or {}))
        adapter = YandexMarketAdapter(str(shop.id), credentials)

        date_to = datetime.now(timezone.utc)
        date_from = date_to - timedelta(days=days)

        print(f"Fetching YM finance report from {date_from.date()} to {date_to.date()}...", flush=True)
        finance = await adapter.get_finance_report(date_from, date_to)
        print(f"Parsed {len(finance)} SKU expense rows", flush=True)

        totals = {}
        for row in finance:
            for k in ["commission", "logistics", "storage", "advertising", "returns", "insurance", "acquiring", "other"]:
                totals[k] = totals.get(k, 0) + float(row.get(k, 0) or 0)
        print(f"Totals: {totals}", flush=True)
        print(f"Grand total: {sum(totals.values())}", flush=True)

        if finance:
            service = SyncService(db)
            await service._update_finance_data(shop.id, finance, date_from, date_to)
            await db.commit()
            print("Saved expenses to Sale rows", flush=True)


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 210
    asyncio.run(main(days))
