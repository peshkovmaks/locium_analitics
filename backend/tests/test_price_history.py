"""Tests for price history persistence and price recommendations.

Run:
    cd backend
    python -m pytest tests/test_price_history.py -v
"""

import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, func

from app.models import (
    Marketplace,
    PriceHistory,
    Product,
    ProductShopMapping,
    Sale,
    Shop,
)
from app.services.sync_service import SyncService


@pytest.fixture
async def ozon_shop(db_session, test_user) -> Shop:
    shop = Shop(
        id=uuid.uuid4(),
        user_id=test_user.id,
        marketplace=Marketplace.ozon,
        name="Ozon Price Shop",
        credentials={},
        is_active=True,
        sync_enabled=True,
        created_at=datetime.utcnow(),
    )
    db_session.add(shop)
    await db_session.commit()
    await db_session.refresh(shop)
    return shop


@pytest.fixture
async def product(db_session, test_user, ozon_shop) -> Product:
    product = Product(
        id=uuid.uuid4(),
        user_id=test_user.id,
        sku="SKU-1",
        canonical_sku="SKU-1",
        name="Test Product",
        cost_price=Decimal("500"),
        min_price=Decimal("0"),
        created_at=datetime.utcnow(),
    )
    db_session.add(product)
    await db_session.flush()
    db_session.add(
        ProductShopMapping(
            id=uuid.uuid4(),
            product_id=product.id,
            shop_id=ozon_shop.id,
            external_sku="SKU-1",
        )
    )
    await db_session.commit()
    await db_session.refresh(product)
    return product


async def history_count(db_session, product_id) -> int:
    result = await db_session.execute(
        select(func.count(PriceHistory.id)).where(
            PriceHistory.product_id == product_id
        )
    )
    return int(result.scalar_one())


def make_price(sku: str, price) -> dict:
    return {
        "external_sku": sku,
        "external_id": f"id-{sku}",
        "price": Decimal(str(price)),
        "discount": 0,
    }


class TestPriceHistorySave:
    async def test_first_price_saved(self, db_session, ozon_shop, product):
        svc = SyncService(db_session)
        saved = await svc._save_price_history(ozon_shop, [make_price("SKU-1", 1000)])
        await db_session.commit()

        assert saved == 1
        assert await history_count(db_session, product.id) == 1

    async def test_same_price_not_duplicated(self, db_session, ozon_shop, product):
        svc = SyncService(db_session)
        await svc._save_price_history(ozon_shop, [make_price("SKU-1", 1000)])
        await db_session.commit()

        saved = await svc._save_price_history(ozon_shop, [make_price("SKU-1", 1000)])
        await db_session.commit()

        assert saved == 0
        assert await history_count(db_session, product.id) == 1

    async def test_changed_price_appended(self, db_session, ozon_shop, product):
        svc = SyncService(db_session)
        await svc._save_price_history(ozon_shop, [make_price("SKU-1", 1000)])
        await db_session.commit()

        saved = await svc._save_price_history(ozon_shop, [make_price("SKU-1", 900)])
        await db_session.commit()

        assert saved == 1
        assert await history_count(db_session, product.id) == 2

        result = await db_session.execute(
            select(PriceHistory.price)
            .where(PriceHistory.product_id == product.id)
            .order_by(PriceHistory.created_at)
        )
        prices = [row.price for row in result.all()]
        assert [str(p) for p in prices] == ["1000.00", "900.00"]

    async def test_price_change_back_and_forth(self, db_session, ozon_shop, product):
        svc = SyncService(db_session)
        for price in (1000, 900, 1000):
            await svc._save_price_history(ozon_shop, [make_price("SKU-1", price)])
            await db_session.commit()

        assert await history_count(db_session, product.id) == 3

    async def test_unknown_sku_skipped(self, db_session, ozon_shop, product):
        svc = SyncService(db_session)
        saved = await svc._save_price_history(
            ozon_shop, [make_price("UNKNOWN", 1000)]
        )
        await db_session.commit()

        assert saved == 0
        assert await history_count(db_session, product.id) == 0


class TestPriceHistoryEndpoint:
    async def test_returns_points(
        self, client, db_session, ozon_shop, product, auth_headers
    ):
        svc = SyncService(db_session)
        await svc._save_price_history(ozon_shop, [make_price("SKU-1", 1000)])
        await db_session.commit()

        resp = await client.get(
            f"/api/v1/products/{product.id}/price-history", headers=auth_headers
        )

        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["price"] == "1000.00"
        assert data[0]["created_at"]

    async def test_date_filter(self, client, db_session, ozon_shop, product, auth_headers):
        svc = SyncService(db_session)
        await svc._save_price_history(ozon_shop, [make_price("SKU-1", 1000)])
        await db_session.commit()

        future = (datetime.utcnow() + timedelta(days=1)).isoformat()
        resp = await client.get(
            f"/api/v1/products/{product.id}/price-history?date_from={future}",
            headers=auth_headers,
        )

        assert resp.status_code == 200
        assert resp.json() == []

    async def test_other_users_product_hidden(
        self, client, db_session, product, auth_headers
    ):
        from app.models import User

        other_user = User(
            id=uuid.uuid4(),
            email="alien@example.com",
            password_hash="fake-hash",
            role="admin",
            created_at=datetime.utcnow(),
        )
        db_session.add(other_user)
        await db_session.flush()
        other = Product(
            id=uuid.uuid4(),
            user_id=other_user.id,
            sku="ALIEN",
            canonical_sku="ALIEN",
            name="Alien",
            created_at=datetime.utcnow(),
        )
        db_session.add(other)
        await db_session.commit()

        resp = await client.get(
            f"/api/v1/products/{other.id}/price-history", headers=auth_headers
        )

        assert resp.status_code == 404


class TestPriceRecommendation:
    async def _add_sale(
        self,
        db_session,
        shop,
        sku: str,
        price,
        commission,
        logistics,
        quantity: int = 1,
        days_ago: int = 0,
    ):
        sale = Sale(
            id=uuid.uuid4(),
            shop_id=shop.id,
            date=datetime.utcnow() - timedelta(days=days_ago),
            external_sku=sku,
            external_id=f"order-{sku}-{uuid.uuid4()}",
            quantity=quantity,
            price=Decimal(str(price)),
            customer_price=Decimal(str(price)),
            revenue=Decimal(str(price)) * quantity,
            commission=Decimal(str(commission)),
            logistics=Decimal(str(logistics)),
        )
        db_session.add(sale)

    async def test_recommendation_raise(self, client, db_session, ozon_shop, product, auth_headers):
        # Себестоимость 500, комиссия+логистика 20% от выручки, маржа 15%:
        # recommended = 500 / (1 - 0.2 - 0.15) = 500 / 0.65 ≈ 769.23
        # Текущая цена 700 < 769.23 * 0.97 → raise
        await self._add_sale(db_session, ozon_shop, "SKU-1", 700, 120, 20)
        await db_session.commit()

        resp = await client.get("/api/v1/products", headers=auth_headers)

        assert resp.status_code == 200
        row = next(p for p in resp.json() if p["sku"] == "SKU-1")
        rec = row["price_recommendation"]
        assert rec["action"] == "raise"
        assert Decimal(rec["recommended_price"]) == Decimal("769.23")
        assert Decimal(rec["min_price"]) == Decimal("700")

    async def test_recommendation_lower_within_tolerance(
        self, client, db_session, ozon_shop, product, auth_headers
    ):
        # Текущая цена 770 при rate 20%: рекомендация 769.23, отклонение ~0.1% < 3% → keep
        await self._add_sale(db_session, ozon_shop, "SKU-1", 770, 140, 14)
        await db_session.commit()

        resp = await client.get("/api/v1/products", headers=auth_headers)

        row = next(p for p in resp.json() if p["sku"] == "SKU-1")
        rec = row["price_recommendation"]
        assert rec["action"] == "keep"
        assert Decimal(rec["recommended_price"]) == Decimal("769.23")

    async def test_recommendation_lower(self, client, db_session, ozon_shop, product, auth_headers):
        # Текущая цена 800 при rate 20%: рекомендация 769.23, 800 > 769.23 * 1.03 → lower
        await self._add_sale(db_session, ozon_shop, "SKU-1", 800, 140, 20)
        await db_session.commit()

        resp = await client.get("/api/v1/products", headers=auth_headers)

        row = next(p for p in resp.json() if p["sku"] == "SKU-1")
        rec = row["price_recommendation"]
        assert rec["action"] == "lower"
        assert Decimal(rec["recommended_price"]) == Decimal("769.23")

    async def test_no_cost_no_recommendation(
        self, client, db_session, ozon_shop, product, auth_headers
    ):
        product.cost_price = Decimal("0")
        await self._add_sale(db_session, ozon_shop, "SKU-1", 700, 120, 20)
        await db_session.commit()

        resp = await client.get("/api/v1/products", headers=auth_headers)

        row = next(p for p in resp.json() if p["sku"] == "SKU-1")
        assert row["price_recommendation"] is None

    async def test_old_sales_ignored(self, client, db_session, ozon_shop, product, auth_headers):
        # Продажа старше 30 дней не должна влиять на расчёт.
        await self._add_sale(
            db_session, ozon_shop, "SKU-1", 700, 120, 20, days_ago=60
        )
        await db_session.commit()

        resp = await client.get("/api/v1/products", headers=auth_headers)

        row = next(p for p in resp.json() if p["sku"] == "SKU-1")
        assert row["price_recommendation"] is None
