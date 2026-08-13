"""Celery tasks for background jobs."""

from datetime import datetime, timedelta
from app.celery_app import celery_app
from app.database import AsyncSessionLocal
from app.services.sync_service import SyncService
from app.services.telegram_bot import TelegramBotService
from sqlalchemy import select
from app.models import User


@celery_app.task
def sync_all_shops_task():
    """Sync all active shops every 4 hours."""
    import asyncio
    asyncio.run(_async_sync_all_shops())


async def _async_sync_all_shops():
    async with AsyncSessionLocal() as db:
        sync_service = SyncService(db)
        results = await sync_service.sync_all_active_shops(days_back=1)
        print(f"Sync completed: {results}")
        return results


@celery_app.task
def send_daily_report_task():
    """Send daily report at 21:00."""
    import asyncio
    asyncio.run(_async_send_daily_reports())


async def _async_send_daily_reports():
    async with AsyncSessionLocal() as db:
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
def check_alerts_task():
    """Check for price/stock/DDR alerts every hour."""
    import asyncio
    asyncio.run(_async_check_alerts())


async def _async_check_alerts():
    """Check all alerts and send notifications."""
    async with AsyncSessionLocal() as db:
        from sqlalchemy import select
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
