"""Telegram bot service for alerts and daily reports."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models import User, Shop, Sale, Stock, Product, Advert
from app.config import get_settings

# Try to import telegram bot, but don't fail if not installed
try:
    from telegram import Bot
    from telegram.constants import ParseMode
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False


class TelegramBotService:
    """Service for sending Telegram notifications."""

    def __init__(self):
        self.settings = get_settings()
        self.bot: Optional[Any] = None
        if TELEGRAM_AVAILABLE and self.settings.telegram_bot_token:
            self.bot = Bot(token=self.settings.telegram_bot_token)

    async def send_daily_report(self, db: AsyncSession, user_id: str) -> bool:
        """Send daily report to user at 21:00.

        Includes:
        - Total revenue, net profit, DRR
        - Breakdown by marketplace
        - Top 3 products by profit
        """
        if not self.bot or not self.settings.telegram_chat_id:
            return False

        # Get user's data for today
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        yesterday = today - timedelta(days=1)

        # Get shops
        result = await db.execute(select(Shop).where(Shop.user_id == user_id))
        shops = result.scalars().all()
        shop_ids = [s.id for s in shops]

        if not shop_ids:
            return False

        # Get today's sales
        sales_result = await db.execute(
            select(Sale).where(
                Sale.shop_id.in_(shop_ids),
                Sale.date >= today,
                Sale.is_return == False,
            )
        )
        sales = sales_result.scalars().all()

        # Get adverts
        adverts_result = await db.execute(
            select(Advert).where(Advert.shop_id.in_(shop_ids), Advert.date >= today)
        )
        adverts = adverts_result.scalars().all()

        # Calculate metrics
        total_revenue = sum(s.revenue for s in sales)
        total_expenses = sum(s.commission + s.logistics + s.storage + s.advertising + s.returns + s.other for s in sales)
        total_ads = sum(a.spend for a in adverts)

        # Get products for cost calculation
        products_result = await db.execute(select(Product).where(Product.user_id == user_id))
        products = {p.sku: p for p in products_result.scalars().all()}

        total_cost = sum(
            products[s.external_sku].cost_price * s.quantity
            for s in sales if s.external_sku in products
        )

        gross = total_revenue - total_expenses
        net = gross - total_cost
        drr = (total_ads / total_revenue * 100) if total_revenue > 0 else Decimal(0)

        # By marketplace
        mp_data = {}
        for shop in shops:
            shop_sales = [s for s in sales if s.shop_id == shop.id]
            shop_adverts = [a for a in adverts if a.shop_id == shop.id]
            rev = sum(s.revenue for s in shop_sales)
            exp = sum(s.commission + s.logistics + s.storage + s.advertising + s.returns + s.other for s in shop_sales)
            cost = sum(products[s.external_sku].cost_price * s.quantity for s in shop_sales if s.external_sku in products)
            net_mp = rev - exp - cost
            margin = (net_mp / rev * 100) if rev > 0 else Decimal(0)
            mp_name = {"wb": "WB", "ozon": "Ozon", "ym": "ЯМ"}.get(shop.marketplace.value, shop.marketplace.value)
            mp_data[mp_name] = {"revenue": rev, "margin": margin}

        # Top 3 products
        product_profits: Dict[str, Decimal] = {}
        for s in sales:
            if s.external_sku not in products:
                continue
            p = products[s.external_sku]
            profit_per_unit = s.revenue - (s.commission + s.logistics + s.storage + s.advertising + s.returns + s.other) - p.cost_price
            total_profit = profit_per_unit * s.quantity
            product_profits[p.name] = product_profits.get(p.name, Decimal(0)) + total_profit

        top_products = sorted(product_profits.items(), key=lambda x: x[1], reverse=True)[:3]

        # Build message
        date_str = today.strftime("%d.%m.%Y")
        message = f"""📊 Отчёт за {date_str}

💰 Выручка: {self._format_money(total_revenue)}
📈 Чистая прибыль: {self._format_money(net)}
📉 ДРР: {drr:.1f}%

По площадкам:
"""
        for mp_name, data in mp_data.items():
            message += f"• {mp_name}: {self._format_money(data['revenue'])} | маржа {data['margin']:.0f}%\n"

        if top_products:
            message += "\nТоп-3 товара:\n"
            for name, profit in top_products:
                message += f"• {name[:30]}... — {self._format_money(profit)}\n"

        try:
            await self.bot.send_message(
                chat_id=self.settings.telegram_chat_id,
                text=message,
                parse_mode=ParseMode.HTML if TELEGRAM_AVAILABLE else None,
            )
            return True
        except Exception as e:
            print(f"Failed to send Telegram message: {e}")
            return False

    async def send_price_alert(self, sku: str, name: str, marketplace: str, current_price: Decimal, min_price: Decimal) -> bool:
        """Send alert when price drops below minimum."""
        if not self.bot or not self.settings.telegram_chat_id:
            return False

        message = f"""🚨 Алерт: Цена ниже минимальной!

Товар: {name} ({sku})
Площадка: {marketplace}
Текущая цена: {self._format_money(current_price)}
Минимальная: {self._format_money(min_price)}"""

        try:
            await self.bot.send_message(
                chat_id=self.settings.telegram_chat_id,
                text=message,
            )
            return True
        except Exception:
            return False

    async def send_drr_alert(self, sku: str, name: str, drr: Decimal) -> bool:
        """Send alert when DRR exceeds threshold."""
        if not self.bot or not self.settings.telegram_chat_id:
            return False

        message = f"""⚠️ Алерт: Высокий ДРР!

Товар: {name} ({sku})
ДРР: {drr:.1f}% (целевой: ≤ 12%)"""

        try:
            await self.bot.send_message(
                chat_id=self.settings.telegram_chat_id,
                text=message,
            )
            return True
        except Exception:
            return False

    async def send_stock_alert(self, sku: str, name: str, marketplace: str, stock: int) -> bool:
        """Send alert when stock is low."""
        if not self.bot or not self.settings.telegram_chat_id:
            return False

        message = f"""⚠️ Алерт: Низкий остаток!

Товар: {name} ({sku})
Площадка: {marketplace}
Остаток: {stock} шт"""

        try:
            await self.bot.send_message(
                chat_id=self.settings.telegram_chat_id,
                text=message,
            )
            return True
        except Exception:
            return False

    def _format_money(self, amount: Decimal) -> str:
        return f"₽ {int(amount):,}".replace(",", " ")
