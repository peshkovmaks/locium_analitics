"""Sync WB orders and sales for the past 90 days, skipping finance/balance/adverts.

This avoids burning WB rate limits on non-essential endpoints while we wait for
the finance API cooldown.
"""

import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

from sqlalchemy import select, func
from app.database import get_async_session_maker
from app.models import Shop, Sale
from app.services.sync_service import SyncService
from app.utils.encryption import decrypt_dict
from app.adapters.wildberries import WildberriesAdapter


SHOP_ID = "56ced321-2e4f-4a1d-ba4f-f3c3e7369cb5"
CHUNK_DAYS = 30


async def main():
    session_maker = get_async_session_maker()
    async with session_maker() as db:
        result = await db.execute(select(Shop).where(Shop.id == SHOP_ID))
        shop = result.scalar_one()
        credentials = decrypt_dict(dict(shop.credentials or {}))

        adapter = WildberriesAdapter(str(shop.id), credentials)
        service = SyncService(db)

        end = datetime.utcnow()
        start = (end - timedelta(days=90)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        current = start
        while current < end:
            chunk_end = min(current + timedelta(days=CHUNK_DAYS), end)
            print(f"\n=== Syncing {current.date()} .. {chunk_end.date()} ===")

            # Skip chunks that already have sales data to save rate limit budget.
            existing_count = await db.scalar(
                select(func.count())
                .select_from(Sale)
                .where(
                    Sale.shop_id == shop.id,
                    Sale.date >= current,
                    Sale.date < chunk_end,
                )
            ) or 0
            print(f"Existing sales in chunk: {existing_count}")
            if existing_count >= 10:
                print("Chunk already has data, skipping.")
                current = chunk_end
                continue

            # Orders -> sales
            try:
                orders = await adapter.get_orders(current, chunk_end)
                await service._save_orders_as_sales(shop.id, orders)
                print(f"Orders saved: {len(orders)}")
            except Exception as e:
                print(f"Orders failed: {e}")

            # Sales
            try:
                sales = await adapter.get_sales(current, chunk_end)
                await service._save_sales(shop.id, sales)
                print(f"Sales saved: {len(sales)}")
            except Exception as e:
                print(f"Sales failed: {e}")

            await db.commit()
            current = chunk_end

            if current < end:
                print("Waiting 70s for WB statistics rate limit...")
                await asyncio.sleep(70)

        print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
