"""One-off script: full historical Wildberries sync for the past 90 days.

WB statistics API keeps sales/orders data for ~90 days.
Rate limit: ~1 request per minute, so the script runs slowly on purpose.
"""

import asyncio

from sqlalchemy import select

from app.database import get_async_session_maker
from app.models import Shop, Marketplace
from app.services.sync_service import SyncService
from app.utils.encryption import decrypt_dict


async def main():
    session_maker = get_async_session_maker()
    async with session_maker() as db:
        shop = (
            await db.execute(select(Shop).where(Shop.marketplace == Marketplace.wb))
        ).scalars().first()

        if not shop:
            print("WB shop not found in DB. Create it first via POST /api/v1/shops")
            return

        credentials = decrypt_dict(dict(shop.credentials or {}))
        service = SyncService(db)
        print("Starting WB initial sync. This will take ~10-15 minutes due to rate limits.")
        result = await service.initial_sync(
            shop,
            days_back=90,
            credentials=credentials,
        )
        print(result)


if __name__ == "__main__":
    asyncio.run(main())
