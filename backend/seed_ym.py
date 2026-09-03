#!/usr/bin/env python3
"""Seed an admin user and the Yandex Market shop from .env credentials.

The DB is expected to be empty (e.g. after a Docker reset). This script creates:
- one admin user
- one Yandex Market shop with the credentials stored in backend/.env
"""
import asyncio
import os
import sys
from uuid import UUID

from dotenv import load_dotenv

load_dotenv()

# Run under x86_64 because the venv contains x86_64 native pydantic_core.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database import get_async_session_maker
from app.models import User, Shop, Marketplace, UserRole
from app.auth import get_password_hash
from app.utils.encryption import encrypt_dict


YM_SHOP_ID = UUID("b87e8b29-f280-46f2-b25d-9055e9325401")
DEFAULT_EMAIL = "admin@locium.local"
DEFAULT_PASSWORD = "admin"


async def main():
    api_key = os.getenv("YM_API_KEY", "").strip()
    business_id = os.getenv("YM_BUSINESS_ID", "").strip()
    campaign_id = os.getenv("YM_CAMPAIGN_ID", "").strip()

    if not api_key or not business_id or not campaign_id:
        print("YM_API_KEY, YM_BUSINESS_ID and YM_CAMPAIGN_ID must be set in .env")
        return

    session_maker = get_async_session_maker()
    async with session_maker() as db:
        existing_user = (await db.execute(select(User).where(User.email == DEFAULT_EMAIL))).scalars().first()
        if existing_user:
            print(f"User {DEFAULT_EMAIL} already exists, id={existing_user.id}")
            user = existing_user
        else:
            user = User(
                email=DEFAULT_EMAIL,
                password_hash=get_password_hash(DEFAULT_PASSWORD),
                role=UserRole.admin,
            )
            db.add(user)
            await db.flush()
            print(f"Created admin user: {DEFAULT_EMAIL} / {DEFAULT_PASSWORD}, id={user.id}")

        existing_shop = (await db.execute(select(Shop).where(Shop.id == YM_SHOP_ID))).scalars().first()
        if existing_shop:
            print(f"YM shop already exists: {existing_shop.id}")
        else:
            credentials = encrypt_dict({
                "api_key": api_key,
                "business_id": business_id,
                "campaign_id": campaign_id,
            })
            shop = Shop(
                id=YM_SHOP_ID,
                user_id=user.id,
                marketplace=Marketplace.yandex_market,
                name="Яндекс Маркет",
                credentials=credentials,
                is_active=True,
                sync_enabled=True,
            )
            db.add(shop)
            await db.commit()
            print(f"Created YM shop: {shop.id}")


if __name__ == "__main__":
    from sqlalchemy import select
    asyncio.run(main())
