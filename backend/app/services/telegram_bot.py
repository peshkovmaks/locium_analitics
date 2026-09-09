"""Telegram bot service for alerts and daily reports."""

from datetime import datetime, timedelta
from decimal import Decimal
from typing import List, Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.models import User, Shop, Sale, Stock, Product, Advert, SyncLog, ShopBalance
from app.config import get_settings
from app.services.metrics import (
    to_decimal as _to_decimal,
    sale_expenses as _sale_expenses,
    gross_revenue,
    buyer_revenue,
)

# Try to import telegram bot, but don't fail if not installed
try:
    from telegram import Bot
    from telegram.constants import ParseMode
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False


# In-memory alert throttle: (alert_type, sku, marketplace) -> last sent time.
# Prevents hourly Celery checks from re-sending the same alert every run.
_alert_throttle: Dict[tuple, datetime] = {}


class TelegramBotService:
    """Service for sending Telegram notifications."""
    def __init__(self):
        self.settings = get_settings()
        self.bot: Optional[Any] = None
        if TELEGRAM_AVAILABLE and self.settings.telegram_bot_token:
            self.bot = Bot(token=self.settings.telegram_bot_token)

    @staticmethod
    def _throttle(alert_type: str, sku: str, marketplace: str, hours: int = 6) -> bool:
        """Return True if the alert may be sent; suppress repeats within `hours`."""
        key = (alert_type, sku, marketplace)
        now = datetime.utcnow()
        last = _alert_throttle.get(key)
        if last and now - last < timedelta(hours=hours):
            return False
        _alert_throttle[key] = now
        return True

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
        total_revenue = sum(gross_revenue(s) for s in sales)
        total_actual = sum(buyer_revenue(s) for s in sales)
        total_expenses = sum(_sale_expenses(s) for s in sales)
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
            rev = sum(gross_revenue(s) for s in shop_sales)
            exp = sum(_sale_expenses(s) for s in shop_sales)
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
            profit_per_unit = gross_revenue(s) - _sale_expenses(s) - p.cost_price * (s.quantity or 0)
            product_profits[p.name] = product_profits.get(p.name, Decimal(0)) + profit_per_unit

        top_products = sorted(product_profits.items(), key=lambda x: x[1], reverse=True)[:3]

        # Build message
        date_str = today.strftime("%d.%m.%Y")
        message = f"""📊 Отчёт за {date_str}

💰 Выручка: {self._format_money(total_revenue)}
💳 Фактическая выручка: {self._format_money(total_actual)}
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

    async def send_morning_report(self, db: AsyncSession, user_id: str) -> bool:
        """Send morning report at 9:00 with yesterday's summary and alerts.

        Includes:
        - Revenue, orders, returns for yesterday
        - Net profit and margin
        - Expenses by category
        - Breakdown by marketplace
        - Top 3 products by profit
        - Low stock alerts
        - Last sync status per shop
        - Current balances
        """
        if not self.bot or not self.settings.telegram_chat_id:
            return False

        # Yesterday in UTC
        now = datetime.utcnow()
        yesterday_start = (now - timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        yesterday_end = yesterday_start.replace(
            hour=23, minute=59, second=59, microsecond=999999
        )

        # Get user's active shops
        result = await db.execute(
            select(Shop).where(
                Shop.user_id == user_id,
                Shop.is_active == True,
            )
        )
        shops = result.scalars().all()
        shop_ids = [s.id for s in shops]
        if not shop_ids:
            return False

        # Sales and returns for yesterday
        sales_result = await db.execute(
            select(Sale).where(
                Sale.shop_id.in_(shop_ids),
                Sale.date >= yesterday_start,
                Sale.date <= yesterday_end,
            )
        )
        sales = sales_result.scalars().all()
        sales_no_return = [s for s in sales if not s.is_return]
        returns = [s for s in sales if s.is_return]

        # Products for cost and names
        products_result = await db.execute(
            select(Product).where(Product.user_id == user_id)
        )
        products = {p.sku: p for p in products_result.scalars().all()}

        # Calculate main metrics
        total_revenue = sum(gross_revenue(s) for s in sales_no_return)
        total_actual = sum(buyer_revenue(s) for s in sales_no_return)
        total_returns = sum(gross_revenue(r) for r in returns)
        total_orders = len(sales_no_return)
        total_units = sum(s.quantity for s in sales_no_return)

        total_expenses = sum(
            _sale_expenses(s) for s in sales_no_return
        )
        total_cost = sum(
            _to_decimal(products[s.external_sku].cost_price) * (s.quantity or 0)
            for s in sales_no_return
            if s.external_sku in products
        )
        gross = total_revenue - total_expenses
        net = gross - total_cost
        margin = (net / total_revenue * 100) if total_revenue > 0 else Decimal(0)

        # By marketplace
        mp_names = {
            "wb": "WB",
            "ozon": "Ozon",
            "ym": "ЯМ",
        }
        mp_data = {}
        for shop in shops:
            mp_sales = [s for s in sales_no_return if s.shop_id == shop.id]
            mp_returns = [r for r in returns if r.shop_id == shop.id]
            rev = sum(gross_revenue(s) for s in mp_sales)
            ret = sum(gross_revenue(r) for r in mp_returns)
            exp = sum(_sale_expenses(s) for s in mp_sales)
            cost = sum(
                _to_decimal(products[s.external_sku].cost_price) * (s.quantity or 0)
                for s in mp_sales
                if s.external_sku in products
            )
            net_mp = rev - exp - cost
            mp_data[shop.id] = {
                "name": mp_names.get(shop.marketplace.value, shop.marketplace.value),
                "shop_name": shop.name,
                "revenue": rev,
                "returns": ret,
                "orders": len(mp_sales),
                "net": net_mp,
            }

        # Top 3 products by units sold
        product_units: Dict[str, int] = {}
        for s in sales_no_return:
            p = products.get(s.external_sku)
            name = p.name if p else s.external_sku
            product_units[name] = product_units.get(name, 0) + (s.quantity or 0)

        top_products = sorted(
            product_units.items(), key=lambda x: x[1], reverse=True
        )[:3]

        # Current balances
        balances_result = await db.execute(
            select(ShopBalance).where(ShopBalance.shop_id.in_(shop_ids))
        )
        balances = {b.shop_id: b for b in balances_result.scalars().all()}

        # Build message
        date_str = yesterday_start.strftime("%d.%m.%Y")
        message = f"""☀️ Доброе утро! Отчёт за {date_str}

💰 Выручка: {self._format_money(total_revenue)}
💳 Фактическая выручка: {self._format_money(total_actual)}
🧾 Заказов: {total_orders} ({total_units} шт)
↩️ Возвраты: {self._format_money(total_returns)}
📈 Чистая прибыль: {self._format_money(net)}
📊 Маржа: {margin:.1f}%

По площадкам:
"""
        for shop in shops:
            data = mp_data.get(shop.id, {})
            if data.get("revenue", 0) > 0 or data.get("orders", 0) > 0:
                message += (
                    f"• {data['name']} ({data['shop_name']}): "
                    f"{self._format_money(data['revenue'])} | "
                    f"{data['orders']} заказов | "
                    f"прибыль {self._format_money(data['net'])}\n"
                )
            else:
                message += f"• {data['name']} ({data['shop_name']}): нет продаж\n"

        if top_products:
            message += "\n🏆 Топ-3 товара по количеству:\n"
            for name, units in top_products:
                message += f"• {name[:30]} — {units} шт\n"

        message += "\n💳 Балансы:\n"
        for shop in shops:
            b = balances.get(shop.id)
            mp = mp_names.get(shop.marketplace.value, shop.marketplace.value)
            if not b or b.is_supported is False:
                message += f"• {mp} ({shop.name}): не поддерживается\n"
            else:
                message += f"• {mp} ({shop.name}): {self._format_money(b.balance)}\n"

        try:
            await self.bot.send_message(
                chat_id=self.settings.telegram_chat_id,
                text=message,
                parse_mode=ParseMode.HTML if TELEGRAM_AVAILABLE else None,
            )
            return True
        except Exception as e:
            print(f"Failed to send morning report: {e}")
            return False

    async def send_price_alert(self, sku: str, name: str, marketplace: str, current_price: Decimal, min_price: Decimal) -> bool:
        """Send alert when price drops below minimum."""
        if not self.bot or not self.settings.telegram_chat_id:
            return False
        if not self._throttle("price", sku, marketplace):
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
        if not self._throttle("drr", sku, "all"):
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
        if not self._throttle("stock", sku, marketplace):
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
