"""Tests for Telegram bot services."""

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio

from app.models import (
    Marketplace,
    Product,
    Sale,
    Shop,
    ShopBalance,
    ShopProduct,
    Stock,
    SyncLog,
    User,
)
from app.services.telegram_bot import TelegramBotService


@pytest_asyncio.fixture
async def morning_shops(db_session, test_user) -> list[Shop]:
    """Create active shops for morning report tests."""
    wb = Shop(
        id=uuid.uuid4(),
        user_id=test_user.id,
        marketplace=Marketplace.wb,
        name="WB Shop",
        credentials={},
        is_active=True,
        sync_enabled=True,
    )
    ozon = Shop(
        id=uuid.uuid4(),
        user_id=test_user.id,
        marketplace=Marketplace.ozon,
        name="Ozon Shop",
        credentials={},
        is_active=True,
        sync_enabled=True,
    )
    ym = Shop(
        id=uuid.uuid4(),
        user_id=test_user.id,
        marketplace=Marketplace.yandex_market,
        name="YM Shop",
        credentials={},
        is_active=True,
        sync_enabled=True,
    )
    db_session.add_all([wb, ozon, ym])
    await db_session.commit()
    for shop in [wb, ozon, ym]:
        await db_session.refresh(shop)
    return [wb, ozon, ym]


@pytest_asyncio.fixture
async def morning_products(db_session, test_user) -> list[Product]:
    """Create products for morning report tests."""
    p1 = Product(
        id=uuid.uuid4(),
        user_id=test_user.id,
        sku="SKU-1",
        name="Product One",
        cost_price=Decimal("100.00"),
    )
    p2 = Product(
        id=uuid.uuid4(),
        user_id=test_user.id,
        sku="SKU-2",
        name="Product Two",
        cost_price=Decimal("50.00"),
    )
    db_session.add_all([p1, p2])
    await db_session.commit()
    await db_session.refresh(p1)
    await db_session.refresh(p2)
    return [p1, p2]


@pytest_asyncio.fixture
async def shop_products_mapping(
    db_session, morning_shops, morning_products
) -> list[ShopProduct]:
    """Create shop-product mappings for morning report tests."""
    wb, ozon, _ = morning_shops
    p1, p2 = morning_products
    mappings = [
        ShopProduct(
            id=uuid.uuid4(),
            shop_id=wb.id,
            product_id=p1.id,
            external_sku=p1.sku,
            external_id="ext-1",
            is_active=True,
        ),
        ShopProduct(
            id=uuid.uuid4(),
            shop_id=ozon.id,
            product_id=p2.id,
            external_sku=p2.sku,
            external_id="ext-2",
            is_active=True,
        ),
    ]
    db_session.add_all(mappings)
    await db_session.commit()
    await db_session.refresh(mappings[0])
    await db_session.refresh(mappings[1])
    return mappings


@pytest_asyncio.fixture
async def yesterday_sales(db_session, morning_shops, morning_products) -> list[Sale]:
    """Create yesterday sales for morning report tests."""
    wb, ozon, _ = morning_shops
    p1, p2 = morning_products
    yesterday = datetime.utcnow() - timedelta(days=1)
    base_time = yesterday.replace(hour=12, minute=0, second=0, microsecond=0)

    sales = [
        Sale(
            id=uuid.uuid4(),
            shop_id=wb.id,
            date=base_time,
            external_sku=p1.sku,
            external_id="order-1",
            quantity=2,
            price=Decimal("500.00"),
            revenue=Decimal("1000.00"),
            commission=Decimal("100.00"),
            logistics=Decimal("50.00"),
            advertising=Decimal("30.00"),
            is_return=False,
        ),
        Sale(
            id=uuid.uuid4(),
            shop_id=ozon.id,
            date=base_time,
            external_sku=p2.sku,
            external_id="order-2",
            quantity=1,
            price=Decimal("300.00"),
            revenue=Decimal("300.00"),
            commission=Decimal("30.00"),
            logistics=Decimal("20.00"),
            advertising=Decimal("10.00"),
            is_return=False,
        ),
        Sale(
            id=uuid.uuid4(),
            shop_id=wb.id,
            date=base_time,
            external_sku=p1.sku,
            external_id="return-1",
            quantity=1,
            price=Decimal("500.00"),
            revenue=Decimal("500.00"),
            is_return=True,
        ),
    ]
    db_session.add_all(sales)
    await db_session.commit()
    return sales


@pytest_asyncio.fixture
async def low_stock(db_session, morning_shops, morning_products, shop_products_mapping) -> Stock:
    """Create low stock record for morning report tests."""
    wb = morning_shops[0]
    p1 = morning_products[0]
    stock = Stock(
        id=uuid.uuid4(),
        shop_id=wb.id,
        date=datetime.utcnow(),
        external_sku=p1.sku,
        warehouse="MAIN",
        quantity=3,
    )
    db_session.add(stock)
    await db_session.commit()
    return stock


@pytest_asyncio.fixture
async def shop_balances(db_session, morning_shops) -> list[ShopBalance]:
    """Create shop balances for morning report tests."""
    wb, ozon, ym = morning_shops
    balances = [
        ShopBalance(
            id=uuid.uuid4(),
            shop_id=wb.id,
            balance=Decimal("12345.67"),
            currency="RUB",
            is_supported=True,
            updated_at=datetime.utcnow(),
        ),
        ShopBalance(
            id=uuid.uuid4(),
            shop_id=ozon.id,
            balance=Decimal("9876.54"),
            currency="RUB",
            is_supported=True,
            updated_at=datetime.utcnow(),
        ),
        ShopBalance(
            id=uuid.uuid4(),
            shop_id=ym.id,
            balance=Decimal("0"),
            currency="RUB",
            is_supported=False,
            updated_at=datetime.utcnow(),
        ),
    ]
    db_session.add_all(balances)
    await db_session.commit()
    return balances


@pytest_asyncio.fixture
async def sync_logs(db_session, morning_shops) -> list[SyncLog]:
    """Create sync logs for morning report tests."""
    wb, ozon, ym = morning_shops
    now = datetime.utcnow()
    logs = [
        SyncLog(
            id=uuid.uuid4(),
            shop_id=wb.id,
            status="success",
            sections={"orders": "success", "balance": "success"},
            created_at=now,
        ),
        SyncLog(
            id=uuid.uuid4(),
            shop_id=ozon.id,
            status="error",
            message="rate limited",
            sections={"orders": "error"},
            created_at=now,
        ),
        SyncLog(
            id=uuid.uuid4(),
            shop_id=ozon.id,
            status="success",
            sections={"orders": "success"},
            created_at=now - timedelta(minutes=5),
        ),
        SyncLog(
            id=uuid.uuid4(),
            shop_id=ym.id,
            status="skipped",
            sections={"balance": "skipped"},
            created_at=now,
        ),
    ]
    db_session.add_all(logs)
    await db_session.commit()
    return logs


class TestSendMorningReport:
    async def test_returns_false_without_config(self, db_session, test_user):
        service = TelegramBotService()
        # Ensure no bot is configured
        service.bot = None
        service.settings.telegram_chat_id = None

        result = await service.send_morning_report(db_session, str(test_user.id))

        assert result is False

    async def test_sends_report_with_all_sections(
        self,
        db_session,
        test_user,
        morning_shops,
        yesterday_sales,
        low_stock,
        shop_balances,
        sync_logs,
    ):
        service = TelegramBotService()
        mock_bot = AsyncMock()
        service.bot = mock_bot
        service.settings.telegram_bot_token = "test-token"
        service.settings.telegram_chat_id = "123456"

        result = await service.send_morning_report(db_session, str(test_user.id))

        assert result is True
        mock_bot.send_message.assert_called_once()
        call_args = mock_bot.send_message.call_args
        text = call_args.kwargs["text"]

        assert "Доброе утро" in text
        assert "Отчёт за" in text
        assert "Выручка" in text
        assert "Заказов" in text
        assert "Возвраты" in text
        assert "Чистая прибыль" in text
        assert "Маржа" in text
        assert "Расходы" in text
        assert "По площадкам" in text
        assert "WB" in text
        assert "Ozon" in text
        assert "ЯМ" in text
        assert "Топ-3 товара" in text
        assert "Product One" in text
        assert "Низкий остаток" in text
        assert "Балансы" in text
        assert "12 345" in text or "12345" in text
        assert "9 876" in text or "9876" in text
        assert "не поддерживается" in text
        assert "Последняя синхронизация" in text
        assert "success" in text
        assert "error" in text
        assert "skipped" in text

    async def test_handles_empty_day(self, db_session, test_user, morning_shops):
        service = TelegramBotService()
        mock_bot = AsyncMock()
        service.bot = mock_bot
        service.settings.telegram_bot_token = "test-token"
        service.settings.telegram_chat_id = "123456"

        result = await service.send_morning_report(db_session, str(test_user.id))

        assert result is True
        text = mock_bot.send_message.call_args.kwargs["text"]
        assert "Доброе утро" in text
        assert "Заказов: 0" in text
