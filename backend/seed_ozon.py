#!/usr/bin/env python3
"""Seed Ozon shop from .env credentials.

Creates an Ozon shop linked to the existing admin user if it does not exist.
"""
import asyncio
import os
import sys
from uuid import uuid4

from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import get_async_session_maker
from app.models import User, Shop, Marketplace
from app.utils.encryption import encrypt_dict


async def main():
    client_id = os.getenv("OZON_CLIENT_ID", "").strip()
    api_key = os.getenv("OZON_API_KEY", "").strip()

    if not client_id or not api_key:
        print("OZON_CLIENT_ID and OZON_API_KEY must be set in .env")
        return

    session_maker = get_async_session_maker()
    async with session_maker() as db:
        admin = (await db.execute(
            select(User).where(User.email == "admin@locium.ru")
        )).scalars().first()
        if not admin:
            print("Admin admin@locium.ru not found. Run seed_ym.py first.")
            return

        existing = (await db.execute(
            select(Shop).where(Shop.marketplace == Marketplace.ozon)
        )).scalars().first()
        if existing:
            print(f"Ozon shop already exists: {existing.id}")
        else:
            credentials = encrypt_dict({
                "client_id": client_id,
                "api_key": api_key,
            })
            shop = Shop(
                id=uuid4(),
                user_id=admin.id,
                marketplace=Marketplace.ozon,
                name="Ozon",
                credentials=credentials,
                is_active=True,
                sync_enabled=True,
            )
            db.add(shop)
            await db.commit()
            print(f"Created Ozon shop: {shop.id}")


if __name__ == "__main__":
    from sqlalchemy import select
    asyncio.run(main())
