"""Tests for the balances router and Yandex Market balance handling.

Run:
    cd backend
    source .venv/bin/activate
    DATABASE_URL=postgresql+asyncpg://mp_user:mp_password@localhost:5432/marketplace_analitics_test pytest tests/test_balances.py -v
"""

import uuid
from datetime import datetime

import pytest
import pytest_asyncio
from decimal import Decimal
from sqlalchemy import select

from app.adapters.yandex_market import YandexMarketAdapter
from app.models import Marketplace, Shop, ShopBalance, User


@pytest_asyncio.fixture
async def ym_shop(db_session, test_user) -> Shop:
    shop = Shop(
        id=uuid.uuid4(),
        user_id=test_user.id,
        marketplace=Marketplace.yandex_market,
        name="YM Test Shop",
        credentials={"api_key": "test-key", "campaign_id": "123"},
        is_active=True,
        sync_enabled=True,
        created_at=datetime.utcnow(),
    )
    db_session.add(shop)
    await db_session.commit()
    await db_session.refresh(shop)
    return shop


@pytest_asyncio.fixture
async def wb_shop(db_session, test_user) -> Shop:
    shop = Shop(
        id=uuid.uuid4(),
        user_id=test_user.id,
        marketplace=Marketplace.wb,
        name="WB Test Shop",
        credentials={"api_key": "test-key"},
        is_active=True,
        sync_enabled=True,
        created_at=datetime.utcnow(),
    )
    db_session.add(shop)
    await db_session.commit()
    await db_session.refresh(shop)
    return shop


class TestYandexMarketAdapter:
    async def test_get_balance_returns_not_supported(self):
        adapter = YandexMarketAdapter(
            shop_id="ym-test",
            credentials={"api_key": "test-key", "campaign_id": "123"},
        )
        data = await adapter.get_balance()

        assert data is not None
        assert data.get("is_supported") is False
        assert data.get("currency") == "RUB"


class TestListBalances:
    async def test_ym_shop_without_balance_is_not_supported(self, client, ym_shop):
        response = await client.get("/api/v1/balances/")

        assert response.status_code == 200
        items = response.json()
        ym_item = next((i for i in items if i["shop_id"] == str(ym_shop.id)), None)
        assert ym_item is not None
        assert ym_item["marketplace"] == "ym"
        assert ym_item["balance"] == "not_supported"

    async def test_ym_shop_with_not_supported_flag(self, client, ym_shop, db_session):
        db_session.add(
            ShopBalance(
                shop_id=ym_shop.id,
                balance=Decimal("0"),
                currency="RUB",
                is_supported=False,
                updated_at=datetime.utcnow(),
            )
        )
        await db_session.commit()

        response = await client.get("/api/v1/balances/")

        assert response.status_code == 200
        items = response.json()
        ym_item = next((i for i in items if i["shop_id"] == str(ym_shop.id)), None)
        assert ym_item is not None
        assert ym_item["marketplace"] == "ym"
        assert ym_item["balance"] == "not_supported"

    async def test_wb_shop_with_balance(self, client, wb_shop, db_session):
        db_session.add(
            ShopBalance(
                shop_id=wb_shop.id,
                balance=Decimal("12345.67"),
                currency="RUB",
                is_supported=True,
                updated_at=datetime.utcnow(),
            )
        )
        await db_session.commit()

        response = await client.get("/api/v1/balances/")

        assert response.status_code == 200
        items = response.json()
        wb_item = next((i for i in items if i["shop_id"] == str(wb_shop.id)), None)
        assert wb_item is not None
        assert wb_item["marketplace"] == "wb"
        assert float(wb_item["balance"]) == 12345.67

    async def test_inactive_shop_not_returned(self, client, test_user, db_session):
        inactive = Shop(
            id=uuid.uuid4(),
            user_id=test_user.id,
            marketplace=Marketplace.ozon,
            name="Inactive Ozon",
            credentials={},
            is_active=False,
            sync_enabled=False,
            created_at=datetime.utcnow(),
        )
        db_session.add(inactive)
        await db_session.commit()

        response = await client.get("/api/v1/balances/")

        assert response.status_code == 200
        items = response.json()
        assert not any(i["shop_id"] == str(inactive.id) for i in items)
