"""Debug script: dump Ozon financial_data for a few postings."""
import asyncio
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select

from app.database import get_async_session_maker
from app.models import Shop, Marketplace
from app.adapters.ozon import OzonAdapter
from app.utils.encryption import decrypt_dict


class DecimalEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Decimal):
            return float(obj)
        return super().default(obj)


async def main():
    session_maker = get_async_session_maker()
    async with session_maker() as db:
        shop = (
            await db.execute(select(Shop).where(Shop.marketplace == Marketplace.ozon))
        ).scalars().first()

        if not shop:
            print("Ozon shop not found")
            return

        credentials = decrypt_dict(dict(shop.credentials or {}))
        adapter = OzonAdapter(str(shop.id), credentials)

        date_to = datetime.utcnow()
        date_from = date_to - timedelta(days=7)

        print(f"Shop: {shop.name} ({shop.id})")
        print(f"Period: {date_from} -> {date_to}")

        items = await adapter._fetch_postings(
            "/v3/posting/fbs/list", date_from, date_to, result_key="postings"
        )

        print(f"Fetched {len(items)} postings")

        for i, item in enumerate(items[:10]):
            posting_number = item.get("posting_number")
            products = item.get("products", [])
            financial = item.get("financial_data") or {}
            print(f"\n--- Posting {i+1}: {posting_number} ---")
            print("financial_data:", json.dumps(financial, indent=2, cls=DecimalEncoder, ensure_ascii=False))
            print("products:", json.dumps(products, indent=2, cls=DecimalEncoder, ensure_ascii=False))
            if i >= 2:
                break


if __name__ == "__main__":
    asyncio.run(main())
