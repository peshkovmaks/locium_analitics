#!/usr/bin/env python3
"""One-off script: resync Yandex Market for the past N days."""

import asyncio
import sys
from datetime import datetime

from sqlalchemy import select

from app.database import get_async_session_maker
from app.models import Shop, Marketplace
from app.services.sync_service import SyncService
from app.utils.encryption import decrypt_dict


async def main(days_back: int = 30, date_from: datetime | None = None, date_to: datetime | None = None):
    session_maker = get_async_session_maker()
    async with session_maker() as db:
        shop = (
            await db.execute(select(Shop).where(Shop.marketplace == Marketplace.yandex_market))
        ).scalars().first()

        if not shop:
            print("Yandex Market shop not found in DB. Create it first via POST /api/v1/shops")
            return

        credentials = decrypt_dict(dict(shop.credentials or {}))
        service = SyncService(db)
        if date_from and date_to:
            print(f"Starting Yandex Market sync from {date_from.date()} to {date_to.date()}...")
            result = await service.sync_shop(
                shop,
                credentials=credentials,
                date_from=date_from,
                date_to=date_to,
                sync_finance=False,
            )
        else:
            print(f"Starting Yandex Market sync for the past {days_back} days...")
            result = await service.sync_shop(
                shop,
                days_back=days_back,
                credentials=credentials,
                sync_finance=False,
            )
        print(result)


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    asyncio.run(main(days))
