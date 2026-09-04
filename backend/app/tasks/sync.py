"""Celery tasks for background jobs."""

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select

from app.celery_app import celery_app
from app.config import get_settings
from app.services.sync_service import SyncService
from app.services.telegram_bot import TelegramBotService
from app.models import User


@asynccontextmanager
async def _task_session():
    """Create a fresh async engine and session for a single Celery task.

    A new engine is needed for each task because Celery tasks run via
    asyncio.run(), which creates a new event loop every time. Reusing an engine
    across different event loops causes 'attached to a different loop' errors.
    """
    settings = get_settings()
    engine = create_async_engine(settings.database_url, echo=False, future=True)
    session_maker = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    async with session_maker() as session:
        try:
            yield session
        finally:
            await session.close()
            await engine.dispose()


@celery_app.task
def sync_all_shops_task():
    """Sync all active shops every 4 hours."""
    asyncio.run(_async_sync_all_shops())


async def _async_sync_all_shops():
    async with _task_session() as db:
        sync_service = SyncService(db)
        results = await sync_service.sync_all_active_shops(days_back=1)
        print(f"Sync completed: {results}")
        return results


@celery_app.task
def sync_wb_finance_task():
    """Sync WB finance reports once a day.

    WB detailed sales reports appear with a multi-day delay, so a 4-hour
    lookback of 1 day never returns anything useful — and each attempt hits
    finance-api.wildberries.ru, which rate-limits by caller IP aggressively.
    A single daily run with a 10-day lookback catches delayed reports without
    poking the blocked endpoint every sync cycle.
    """
    asyncio.run(_async_sync_wb_finance())


async def _async_sync_wb_finance():
    from app.models import Marketplace, Shop
    from app.utils.encryption import decrypt_dict
    from app.adapters.base import AdapterFactory

    async with _task_session() as db:
        result = await db.execute(
            select(Shop).where(
                Shop.is_active == True,
                Shop.sync_enabled == True,
                Shop.marketplace == Marketplace.wb,
            )
        )
        shops = result.scalars().all()
        sync_service = SyncService(db)

        days_back = 10
        end = datetime.utcnow()
        start = (end - timedelta(days=days_back)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        for shop in shops:
            try:
                adapter = AdapterFactory.create(
                    shop.marketplace.value,
                    str(shop.id),
                    decrypt_dict(shop.credentials),
                )
                if not await adapter.authenticate():
                    print(f"WB finance sync: auth failed for shop {shop.id}")
                    continue
                finance = await adapter.get_finance_report(start, end)
                if finance:
                    await sync_service._update_finance_data(shop.id, finance, start, end)
                    await sync_service._save_finance_transactions(
                        shop.id, shop.marketplace, finance, start, end
                    )
                await db.commit()
                print(f"WB finance sync: shop {shop.id}: {len(finance or [])} rows")
            except Exception as e:
                await db.rollback()
                print(f"WB finance sync failed for shop {shop.id}: {type(e).__name__}: {e}")


@celery_app.task
def send_daily_report_task():
    """Send daily report at 21:00."""
    asyncio.run(_async_send_daily_reports())


async def _async_send_daily_reports():
    async with _task_session() as db:
        bot = TelegramBotService()
        # Get all users with telegram configured
        result = await db.execute(select(User))
        users = result.scalars().all()

        for user in users:
            try:
                await bot.send_daily_report(db, str(user.id))
            except Exception as e:
                print(f"Failed to send report to user {user.id}: {e}")


@celery_app.task
def send_morning_report_task():
    """Send morning report at 9:00."""
    asyncio.run(_async_send_morning_reports())


async def _async_send_morning_reports():
    async with _task_session() as db:
        bot = TelegramBotService()
        result = await db.execute(select(User))
        users = result.scalars().all()

        for user in users:
            try:
                await bot.send_morning_report(db, str(user.id))
            except Exception as e:
                print(f"Failed to send morning report to user {user.id}: {e}")


@celery_app.task
def check_alerts_task():
    """Check for price/stock/DDR alerts every hour."""
    asyncio.run(_async_check_alerts())


async def _async_check_alerts():
    """Check all alerts and send notifications."""
    async with _task_session() as db:
        from app.models import Shop, Product, ShopProduct, Stock

        bot = TelegramBotService()

        # Check price alerts
        result = await db.execute(select(ShopProduct).where(ShopProduct.is_active == True))
        shop_products = result.scalars().all()

        for sp in shop_products:
            # Get product
            product_result = await db.execute(select(Product).where(Product.id == sp.product_id))
            product = product_result.scalar_one_or_none()
            if not product:
                continue

            # Get shop
            shop_result = await db.execute(select(Shop).where(Shop.id == sp.shop_id))
            shop = shop_result.scalar_one_or_none()
            if not shop:
                continue

            # Get latest stock/price
            stock_result = await db.execute(
                select(Stock).where(
                    Stock.shop_id == shop.id,
                    Stock.external_sku == sp.external_sku,
                ).order_by(Stock.date.desc()).limit(1)
            )
            stock = stock_result.scalar_one_or_none()

            # Check low stock
            if stock and stock.quantity < 10:
                await bot.send_stock_alert(
                    product.sku, product.name,
                    shop.marketplace.value, stock.quantity
                )

        # Check DRR alerts (simplified — would need actual advert data)
        # This would be expanded with real DRR calculation from sales + adverts
