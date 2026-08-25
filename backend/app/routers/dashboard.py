from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime, timedelta, date
from typing import List, Dict, Optional
from decimal import Decimal

from fastapi import Query

from app.database import get_db
from app.models import User, Shop, Product, Sale, Stock, Advert, Marketplace, FinanceTransaction
from app.schemas import (
    DashboardData,
    KPIData,
    OrderStats,
    MarketplaceKPI,
    AlertItem,
    MarketplaceComparison,
    UnitEconomicsRow,
    UnitEconomicsMarketplaceRow,
    ProductDashboardRow,
    DailyTrendRow,
)
from app.auth import get_current_user

router = APIRouter()

# Expense ratios (should come from config/DB in production)
EXPENSE_RATIOS = {
    "wb": {
        "commission": 0.15,
        "logistics": 0.10,
        "storage": 0.02,
        "ads": 0.08,
        "returns": 0.02,
        "other": 0.01,
    },
    "ozon": {
        "commission": 0.12,
        "logistics": 0.10,
        "storage": 0.02,
        "ads": 0.05,
        "returns": 0.02,
        "other": 0.01,
    },
    "ym": {
        "commission": 0.10,
        "logistics": 0.10,
        "storage": 0.02,
        "ads": 0.04,
        "returns": 0.02,
        "other": 0.01,
    },
}

MP_NAMES = {"wb": "Wildberries", "ozon": "Ozon", "ym": "Яндекс Маркет"}

ALERT_THRESHOLDS = {
    "min_margin": Decimal("15"),  # %
    "max_drr": Decimal("12"),     # %
    "min_stock": 10,              # шт
}


def _to_decimal(value) -> Decimal:
    return Decimal(str(value or 0))


def _sale_expenses(s: Sale) -> Decimal:
    return (
        max(_to_decimal(s.commission), Decimal(0))
        + max(_to_decimal(s.logistics), Decimal(0))
        + max(_to_decimal(s.storage), Decimal(0))
        + max(_to_decimal(s.advertising), Decimal(0))
        + max(_to_decimal(s.returns), Decimal(0))
        + max(_to_decimal(s.insurance), Decimal(0))
        + max(_to_decimal(s.acquiring), Decimal(0))
        + max(_to_decimal(s.other), Decimal(0))
    )


def _gross_revenue(s: Sale) -> Decimal:
    """Gross order amount — the larger of seller price and buyer price."""
    cp = _to_decimal(s.customer_price)
    if cp > 0:
        return max(_to_decimal(s.price), cp) * (s.quantity or 0)
    return _to_decimal(s.revenue)


def _net_revenue(s: Sale) -> Decimal:
    """Net revenue to the seller — the smaller of seller price and buyer price."""
    cp = _to_decimal(s.customer_price)
    if cp > 0:
        return min(_to_decimal(s.price), cp) * (s.quantity or 0)
    return _to_decimal(s.revenue)


def _actual_revenue(s: Sale) -> Decimal:
    """Net revenue plus marketplace discount compensation (e.g. Ozon points)."""
    return _net_revenue(s) + _to_decimal(s.marketplace_discount)


def _shop_expenses(
    shop_id, mp: str, sales, returns, finance_transactions
) -> Decimal:
    if mp == "ozon":
        return sum(
            _to_decimal(t.amount)
            for t in finance_transactions
            if t.shop_id == shop_id
        )
    return sum(_sale_expenses(s) for s in sales if s.shop_id == shop_id) + sum(
        _sale_expenses(r) for r in returns if r.shop_id == shop_id
    )


def _shop_ads(shop_id, mp: str, sales, finance_transactions) -> Decimal:
    if mp == "ozon":
        return sum(
            _to_decimal(t.amount)
            for t in finance_transactions
            if t.shop_id == shop_id and t.category == "advertising"
        )
    return sum(_to_decimal(s.advertising) for s in sales if s.shop_id == shop_id)


def _calc_period_kpis(sales, returns, finance_transactions, adverts, shops, products) -> Dict[str, Decimal]:
    total_revenue = sum(_gross_revenue(s) for s in sales) - sum(
        _gross_revenue(r) for r in returns
    )
    total_actual_revenue = sum(_actual_revenue(s) for s in sales) - sum(
        _actual_revenue(r) for r in returns
    )
    total_expenses = sum(
        _shop_expenses(s.id, s.marketplace.value, sales, returns, finance_transactions)
        for s in shops
    )
    total_ads = sum(
        _shop_ads(s.id, s.marketplace.value, sales, finance_transactions)
        for s in shops
    )
    total_gross = total_revenue - total_expenses
    total_cost = Decimal(0)
    for s in sales:
        sku = s.external_sku
        if sku in products:
            total_cost += _to_decimal(products[sku].cost_price) * (s.quantity or 0)
    total_net = total_gross - total_cost
    drr = (total_ads / total_revenue * 100) if total_revenue > 0 else Decimal(0)
    return {
        "revenue": total_revenue,
        "actual_revenue": total_actual_revenue,
        "gross": total_gross,
        "net": total_net,
        "drr": drr,
    }


def _calc_order_stats(sales, returns, total_net: Optional[Decimal] = None) -> Dict[str, Decimal]:
    """Aggregate order-level metrics for a given period."""
    unique_order_ids = {s.external_id for s in sales}
    orders_count = len(unique_order_ids)
    total_items = sum(s.quantity or 0 for s in sales)
    total_actual_revenue = sum(_actual_revenue(s) for s in sales)

    if total_net is None:
        # Fallback (should not be used when KPI net is available)
        total_expenses = sum(_sale_expenses(s) for s in sales) + sum(
            _sale_expenses(r) for r in returns
        )
        total_gross = Decimal(0)
        if sales:
            total_revenue = sum(_gross_revenue(s) for s in sales)
            total_gross = total_revenue - total_expenses
        total_net = total_gross

    unique_return_ids = {r.external_id for r in returns}
    returns_count = len(unique_return_ids)
    total_orders_with_returns = orders_count + returns_count

    average_check = (total_actual_revenue / orders_count) if orders_count > 0 else Decimal(0)
    average_profit_per_order = (total_net / orders_count) if orders_count > 0 else Decimal(0)
    profit_per_item = (total_net / total_items) if total_items > 0 else Decimal(0)
    return_rate = (
        Decimal(returns_count) / Decimal(total_orders_with_returns) * 100
        if total_orders_with_returns > 0
        else Decimal(0)
    )
    avg_items_per_order = (Decimal(total_items) / Decimal(orders_count)) if orders_count > 0 else Decimal(0)

    return {
        "orders_count": orders_count,
        "average_check": average_check,
        "average_profit_per_order": average_profit_per_order,
        "profit_per_item": profit_per_item,
        "returns_count": returns_count,
        "return_rate": return_rate,
        "avg_items_per_order": avg_items_per_order,
    }


@router.get("/data", response_model=DashboardData)
async def get_dashboard(
    period: str = "today",
    marketplace: str = "all",
    start_date: Optional[date] = Query(None, description="Start date (YYYY-MM-DD). Overrides period if provided together with end_date."),
    end_date: Optional[date] = Query(None, description="End date (YYYY-MM-DD). Overrides period if provided together with start_date."),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Get user's shops
    result = await db.execute(select(Shop).where(Shop.user_id == current_user.id))
    shops = result.scalars().all()

    if marketplace != "all":
        shops = [s for s in shops if s.marketplace.value == marketplace]

    shop_ids = [s.id for s in shops]

    # Get date range
    now = datetime.utcnow()
    if start_date and end_date:
        start_dt = datetime.combine(start_date, datetime.min.time())
        end_dt = datetime.combine(end_date, datetime.max.time())
    elif period == "today":
        start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_dt = now
    elif period == "7d":
        start_dt = (now - timedelta(days=7)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end_dt = now
    else:
        start_dt = (now - timedelta(days=30)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end_dt = now

    # Get sales
    sales_result = await db.execute(
        select(Sale).where(
            Sale.shop_id.in_(shop_ids),
            Sale.date >= start_dt,
            Sale.date <= end_dt,
            Sale.is_return == False,
        )
    )
    sales = sales_result.scalars().all()

    # Get returns
    returns_result = await db.execute(
        select(Sale).where(
            Sale.shop_id.in_(shop_ids),
            Sale.date >= start_dt,
            Sale.date <= end_dt,
            Sale.is_return == True,
        )
    )
    returns = returns_result.scalars().all()

    # Get finance transactions for Ozon — expenses are summed by operation date,
    # not by sale date, so the dashboard matches the Ozon seller dashboard.
    ozon_shop_ids = [s.id for s in shops if s.marketplace == Marketplace.ozon]
    finance_transactions = []
    if ozon_shop_ids:
        finance_result = await db.execute(
            select(FinanceTransaction).where(
                FinanceTransaction.shop_id.in_(ozon_shop_ids),
                FinanceTransaction.operation_date >= start_dt,
                FinanceTransaction.operation_date <= end_dt,
            )
        )
        finance_transactions = finance_result.scalars().all()

    # Get adverts
    adverts_result = await db.execute(
        select(Advert).where(
            Advert.shop_id.in_(shop_ids),
            Advert.date >= start_dt,
            Advert.date <= end_dt,
        )
    )
    adverts = adverts_result.scalars().all()

    # Get products
    products_result = await db.execute(
        select(Product).where(Product.user_id == current_user.id)
    )
    products = {}
    for p in products_result.scalars().all():
        products[p.sku] = p
        if p.canonical_sku and p.canonical_sku != p.sku:
            products[p.canonical_sku] = p

    # Get stocks
    stocks_result = await db.execute(select(Stock).where(Stock.shop_id.in_(shop_ids)))
    stocks = stocks_result.scalars().all()

    def _to_decimal(value) -> Decimal:
        return Decimal(str(value or 0))

    def _sale_expenses(s: Sale) -> Decimal:
        return (
            max(_to_decimal(s.commission), Decimal(0))
            + max(_to_decimal(s.logistics), Decimal(0))
            + max(_to_decimal(s.storage), Decimal(0))
            + max(_to_decimal(s.advertising), Decimal(0))
            + max(_to_decimal(s.returns), Decimal(0))
            + max(_to_decimal(s.insurance), Decimal(0))
            + max(_to_decimal(s.acquiring), Decimal(0))
            + max(_to_decimal(s.other), Decimal(0))
        )

    def _shop_expenses_from_sales(shop_id) -> Decimal:
        mp_sales = [s for s in sales if s.shop_id == shop_id]
        mp_returns = [r for r in returns if r.shop_id == shop_id]
        return sum(_sale_expenses(s) for s in mp_sales) + sum(
            _sale_expenses(r) for r in mp_returns
        )

    def _shop_ads_from_sales(shop_id) -> Decimal:
        return sum(_to_decimal(s.advertising) for s in sales if s.shop_id == shop_id)

    def _shop_expenses(shop_id, mp: str) -> Decimal:
        if mp == "ozon":
            return sum(
                _to_decimal(t.amount)
                for t in finance_transactions
                if t.shop_id == shop_id
            )
        return _shop_expenses_from_sales(shop_id)

    def _shop_ads(shop_id, mp: str) -> Decimal:
        if mp == "ozon":
            return sum(
                _to_decimal(t.amount)
                for t in finance_transactions
                if t.shop_id == shop_id and t.category == "advertising"
            )
        return _shop_ads_from_sales(shop_id)

    def _gross_revenue(s: Sale) -> Decimal:
        """Gross order amount — the larger of seller price and buyer price."""
        cp = _to_decimal(s.customer_price)
        if cp > 0:
            return max(_to_decimal(s.price), cp) * (s.quantity or 0)
        return _to_decimal(s.revenue)

    def _net_revenue(s: Sale) -> Decimal:
        """Net revenue to the seller — the smaller of seller price and buyer price."""
        cp = _to_decimal(s.customer_price)
        if cp > 0:
            return min(_to_decimal(s.price), cp) * (s.quantity or 0)
        return _to_decimal(s.revenue)

    def _buyer_revenue(s: Sale) -> Decimal:
        """Revenue at the buyer-paid price, used for unit economics average price."""
        cp = _to_decimal(s.customer_price)
        if cp > 0:
            return cp * (s.quantity or 0)
        return _to_decimal(s.revenue)

    def _actual_revenue(s: Sale) -> Decimal:
        """Net revenue plus marketplace discount compensation (e.g. Ozon points)."""
        return _net_revenue(s) + _to_decimal(s.marketplace_discount)

    # Calculate KPIs for the current period
    current = _calc_period_kpis(sales, returns, finance_transactions, adverts, shops, products)
    total_revenue = current["revenue"]
    total_actual_revenue = current["actual_revenue"]
    total_gross = current["gross"]
    total_net = current["net"]
    drr = current["drr"]

    # Calculate real WoW: previous period of the same length
    delta = end_dt - start_dt
    prev_start = start_dt - delta
    prev_end = end_dt - delta

    prev_sales_result = await db.execute(
        select(Sale).where(
            Sale.shop_id.in_(shop_ids),
            Sale.date >= prev_start,
            Sale.date <= prev_end,
            Sale.is_return == False,
        )
    )
    prev_sales = prev_sales_result.scalars().all()

    prev_returns_result = await db.execute(
        select(Sale).where(
            Sale.shop_id.in_(shop_ids),
            Sale.date >= prev_start,
            Sale.date <= prev_end,
            Sale.is_return == True,
        )
    )
    prev_returns = prev_returns_result.scalars().all()

    prev_finance_transactions = []
    if ozon_shop_ids:
        prev_finance_result = await db.execute(
            select(FinanceTransaction).where(
                FinanceTransaction.shop_id.in_(ozon_shop_ids),
                FinanceTransaction.operation_date >= prev_start,
                FinanceTransaction.operation_date <= prev_end,
            )
        )
        prev_finance_transactions = prev_finance_result.scalars().all()

    prev_adverts_result = await db.execute(
        select(Advert).where(
            Advert.shop_id.in_(shop_ids),
            Advert.date >= prev_start,
            Advert.date <= prev_end,
        )
    )
    prev_adverts = prev_adverts_result.scalars().all()

    previous = _calc_period_kpis(
        prev_sales,
        prev_returns,
        prev_finance_transactions,
        prev_adverts,
        shops,
        products,
    )

    def _wow_pct(cur: Decimal, prev: Decimal) -> float:
        return float(round((cur - prev) / abs(prev) * 100)) if prev != 0 else 0.0

    revenue_wow = _wow_pct(current["revenue"], previous["revenue"])
    gross_wow = _wow_pct(current["gross"], previous["gross"])
    net_wow = _wow_pct(current["net"], previous["net"])
    drr_wow = float(round(current["drr"] - previous["drr"]))

    # Order-level stats for current and previous periods
    current_order_stats = _calc_order_stats(sales, returns, current["net"])
    previous_order_stats = _calc_order_stats(prev_sales, prev_returns, previous["net"])

    def _wow_order(cur: Decimal, prev: Decimal) -> float:
        return float(round((cur - prev) / abs(prev) * 100)) if prev != 0 else 0.0

    orders_count_wow = _wow_order(
        Decimal(current_order_stats["orders_count"]),
        Decimal(previous_order_stats["orders_count"]),
    )
    average_check_wow = _wow_order(
        current_order_stats["average_check"], previous_order_stats["average_check"]
    )
    average_profit_per_order_wow = _wow_order(
        current_order_stats["average_profit_per_order"],
        previous_order_stats["average_profit_per_order"],
    )
    profit_per_item_wow = _wow_order(
        current_order_stats["profit_per_item"],
        previous_order_stats["profit_per_item"],
    )
    returns_count_wow = _wow_order(
        Decimal(current_order_stats["returns_count"]),
        Decimal(previous_order_stats["returns_count"]),
    )
    return_rate_wow = _wow_order(
        current_order_stats["return_rate"], previous_order_stats["return_rate"]
    )
    avg_items_per_order_wow = _wow_order(
        current_order_stats["avg_items_per_order"],
        previous_order_stats["avg_items_per_order"],
    )

    # Build date range for all trends
    trend_dates = []
    current_day = start_dt.date()
    end_day = end_dt.date()
    while current_day <= end_day:
        trend_dates.append(current_day)
        current_day += timedelta(days=1)

    # Build daily KPI trend for sparklines
    sales_by_day: Dict[date, List[Sale]] = {}
    for s in sales:
        day = s.date.date() if s.date else None
        if day:
            sales_by_day.setdefault(day, []).append(s)

    returns_by_day: Dict[date, List[Sale]] = {}
    for r in returns:
        day = r.date.date() if r.date else None
        if day:
            returns_by_day.setdefault(day, []).append(r)

    adverts_by_day: Dict[date, List[Advert]] = {}
    for a in adverts:
        day = a.date.date() if a.date else None
        if day:
            adverts_by_day.setdefault(day, []).append(a)

    finance_by_day: Dict[date, List[FinanceTransaction]] = {}
    for t in finance_transactions:
        day = t.operation_date.date() if t.operation_date else None
        if day:
            finance_by_day.setdefault(day, []).append(t)

    revenue_trend = []
    actual_revenue_trend = []
    gross_trend = []
    net_trend = []
    drr_trend = []
    orders_count_trend: List[int] = []
    average_check_trend: List[float] = []
    average_profit_per_order_trend: List[float] = []
    profit_per_item_trend: List[float] = []
    returns_count_trend: List[int] = []
    avg_items_per_order_trend: List[float] = []
    for day in trend_dates:
        day_sales = sales_by_day.get(day, [])
        day_returns = returns_by_day.get(day, [])
        day_adverts = adverts_by_day.get(day, [])

        day_revenue = sum(_gross_revenue(s) for s in day_sales) - sum(_gross_revenue(r) for r in day_returns)
        day_actual_revenue = sum(_actual_revenue(s) for s in day_sales) - sum(_actual_revenue(r) for r in day_returns)
        day_expenses = Decimal("0")
        day_ads = Decimal("0")
        for shop in shops:
            if shop.marketplace == Marketplace.ozon:
                day_txs = [t for t in finance_by_day.get(day, []) if t.shop_id == shop.id]
                day_expenses += sum(_to_decimal(t.amount) for t in day_txs)
                day_ads += sum(
                    _to_decimal(t.amount)
                    for t in day_txs
                    if t.category == "advertising"
                )
            else:
                shop_day_sales = [s for s in day_sales if s.shop_id == shop.id]
                shop_day_returns = [r for r in day_returns if r.shop_id == shop.id]
                day_expenses += sum(_sale_expenses(s) for s in shop_day_sales) + sum(
                    _sale_expenses(r) for r in shop_day_returns
                )
                day_ads += sum(_to_decimal(s.advertising) for s in shop_day_sales)
        day_gross = day_revenue - day_expenses
        day_cost = sum(
            _to_decimal(products[s.external_sku].cost_price) * (s.quantity or 0)
            for s in day_sales
            if s.external_sku in products
        )
        day_net = day_gross - day_cost
        day_drr = float(day_ads / day_revenue * 100) if day_revenue > 0 else 0.0

        revenue_trend.append(float(day_revenue))
        actual_revenue_trend.append(float(day_actual_revenue))
        gross_trend.append(float(day_gross))
        net_trend.append(float(day_net))
        drr_trend.append(day_drr)

        day_unique_orders = {s.external_id for s in day_sales}
        day_orders_count = len(day_unique_orders)
        day_items = sum(s.quantity or 0 for s in day_sales)
        day_unique_returns = {r.external_id for r in day_returns}

        orders_count_trend.append(day_orders_count)
        average_check_trend.append(
            float(day_actual_revenue / day_orders_count) if day_orders_count > 0 else 0.0
        )
        average_profit_per_order_trend.append(
            float(day_net / day_orders_count) if day_orders_count > 0 else 0.0
        )
        profit_per_item_trend.append(
            float(day_net / day_items) if day_items > 0 else 0.0
        )
        returns_count_trend.append(len(day_unique_returns))
        avg_items_per_order_trend.append(
            float(Decimal(day_items) / Decimal(day_orders_count)) if day_orders_count > 0 else 0.0
        )

    # Calculate KPI breakdown by marketplace
    kpi_by_marketplace: List[MarketplaceKPI] = []
    for shop in shops:
        mp = shop.marketplace.value
        mp_sales = [s for s in sales if s.shop_id == shop.id]
        mp_returns = [r for r in returns if r.shop_id == shop.id]
        mp_adverts = [a for a in adverts if a.shop_id == shop.id]

        mp_revenue = sum(_gross_revenue(s) for s in mp_sales) - sum(_gross_revenue(r) for r in mp_returns)
        mp_actual_revenue = sum(_actual_revenue(s) for s in mp_sales) - sum(_actual_revenue(r) for r in mp_returns)
        mp_expenses = _shop_expenses(shop.id, mp)
        mp_ads = _shop_ads(shop.id, mp)
        mp_gross = mp_revenue - mp_expenses
        mp_cost = sum(
            _to_decimal(products[s.external_sku].cost_price) * (s.quantity or 0)
            for s in mp_sales
            if s.external_sku in products
        )
        mp_net = mp_gross - mp_cost
        mp_drr = (mp_ads / mp_revenue * 100) if mp_revenue > 0 else Decimal(0)

        kpi_by_marketplace.append(
            MarketplaceKPI(
                marketplace=MP_NAMES.get(mp, mp),
                revenue=mp_revenue,
                actual_revenue=mp_actual_revenue,
                expenses=mp_expenses,
                gross_profit=mp_gross,
                net_profit=mp_net,
                drr=mp_drr,
            )
        )

    kpi = KPIData(
        revenue=total_revenue,
        actual_revenue=total_actual_revenue,
        gross_profit=total_gross,
        net_profit=total_net,
        drr=drr,
        revenue_wow=revenue_wow,
        gross_wow=gross_wow,
        net_wow=net_wow,
        drr_wow=drr_wow,
        by_marketplace=kpi_by_marketplace,
        revenue_trend=revenue_trend,
        gross_trend=gross_trend,
        net_trend=net_trend,
        drr_trend=drr_trend,
    )

    # Marketplace comparison
    mp_comparison: List[MarketplaceComparison] = []
    for shop in shops:
        mp = shop.marketplace.value
        mp_sales_shop = [s for s in sales if s.shop_id == shop.id]
        mp_returns_shop = [r for r in returns if r.shop_id == shop.id]
        rev = sum(_gross_revenue(s) for s in mp_sales_shop) - sum(
            _gross_revenue(r) for r in mp_returns_shop
        )
        exp = _shop_expenses(shop.id, mp)
        gross = rev - exp
        cost = sum(
            _to_decimal(products[s.external_sku].cost_price) * (s.quantity or 0)
            for s in sales
            if s.shop_id == shop.id and s.external_sku in products
        )
        net = gross - cost
        ads = _shop_ads(shop.id, mp)
        drr_mp = (ads / rev * 100) if rev > 0 else Decimal(0)

        mp_comparison.append(
            MarketplaceComparison(
                marketplace=MP_NAMES.get(mp, mp),
                revenue=rev,
                expenses=exp,
                gross_profit=gross,
                net_profit=net,
                net_margin=(net / rev * 100) if rev > 0 else Decimal(0),
                drr=drr_mp,
            )
        )

    # Unit economics — group by product, show per-marketplace rows
    sku_sales: Dict[tuple, List[Sale]] = {}
    for s in sales:
        key = (s.external_sku, s.shop_id)
        sku_sales.setdefault(key, []).append(s)

    # Build daily revenue trend for each (sku, shop) pair over the selected period
    daily_revenue: Dict[tuple, Dict[date, Decimal]] = {}
    daily_actual_revenue: Dict[tuple, Dict[date, Decimal]] = {}
    for s in sales:
        key = (s.external_sku, s.shop_id)
        day = s.date.date() if s.date else None
        if day is None:
            continue
        daily_revenue.setdefault(key, {})
        daily_revenue[key][day] = daily_revenue[key].get(day, Decimal(0)) + _to_decimal(s.revenue)
        daily_actual_revenue.setdefault(key, {})
        daily_actual_revenue[key][day] = daily_actual_revenue[key].get(day, Decimal(0)) + _buyer_revenue(s)

    product_unit_map: Dict[str, dict] = {}
    for (external_sku, shop_id), s_sales in sku_sales.items():
        if external_sku not in products:
            continue
        p = products[external_sku]
        shop = next((sh for sh in shops if sh.id == shop_id), None)
        if not shop:
            continue
        mp = shop.marketplace.value

        total_qty = sum(s.quantity or 0 for s in s_sales)
        total_revenue_sku = sum(_to_decimal(s.revenue) for s in s_sales)
        total_actual_revenue_sku = sum(_buyer_revenue(s) for s in s_sales)
        total_expenses_sku = sum(_sale_expenses(s) for s in s_sales)
        total_ads_sku = sum(_to_decimal(s.advertising) for s in s_sales)

        avg_price = (total_actual_revenue_sku / total_qty) if total_qty > 0 else Decimal(0)
        expense_per_unit = (total_expenses_sku / total_qty) if total_qty > 0 else Decimal(0)
        net_per = avg_price - _to_decimal(p.cost_price) - expense_per_unit
        margin = (net_per / avg_price * 100) if avg_price > 0 else Decimal(0)
        drr_sku = (total_ads_sku / total_revenue_sku * 100) if total_revenue_sku > 0 else Decimal(0)

        if p.sku not in product_unit_map:
            product_unit_map[p.sku] = {
                "sku": p.sku,
                "name": p.name,
                "cost": _to_decimal(p.cost_price),
                "rows": [],
            }

        trend = [
            int(daily_actual_revenue.get((external_sku, shop_id), {}).get(day, Decimal(0)))
            for day in trend_dates
        ]

        product_unit_map[p.sku]["rows"].append(
            UnitEconomicsMarketplaceRow(
                marketplace=MP_NAMES.get(mp, mp),
                sales=total_qty,
                price=avg_price,
                cost=p.cost_price,
                expense_per_unit=expense_per_unit,
                net_per_unit=net_per,
                margin=margin,
                drr=drr_sku,
                trend=trend,
            )
        )

    unit_rows: List[UnitEconomicsRow] = sorted(
        [UnitEconomicsRow(**v) for v in product_unit_map.values()],
        key=lambda x: sum(r.net_per_unit * r.sales for r in x.rows),
        reverse=True,
    )

    # Product dashboard rows
    product_rows: List[ProductDashboardRow] = []
    for p in products.values():
        p_sales = [s for s in sales if s.external_sku == p.sku]
        p_returns = [r for r in returns if r.external_sku == p.sku]
        p_revenue = sum(_to_decimal(s.revenue) for s in p_sales) - sum(
            _to_decimal(r.revenue) for r in p_returns
        )
        p_actual_revenue = sum(_actual_revenue(s) for s in p_sales) - sum(
            _actual_revenue(r) for r in p_returns
        )
        p_expenses = sum(_sale_expenses(s) for s in p_sales) + sum(
            _sale_expenses(r) for r in p_returns
        )
        p_ads = sum(_to_decimal(s.advertising) for s in p_sales)
        p_gross = p_revenue - p_expenses
        p_qty = sum(s.quantity or 0 for s in p_sales)
        p_cost = _to_decimal(p.cost_price) * p_qty
        p_net = p_gross - p_cost
        p_margin = (p_net / p_revenue * 100) if p_revenue > 0 else Decimal(0)
        p_drr = (p_ads / p_revenue * 100) if p_revenue > 0 else Decimal(0)
        p_avg_price = (p_actual_revenue / p_qty) if p_qty > 0 else Decimal(0)

        p_stocks = [st for st in stocks if st.external_sku == p.sku]
        total_stock = sum(st.quantity for st in p_stocks)

        alert_price = (
            p.min_price is not None
            and _to_decimal(p.min_price) > 0
            and p_avg_price > 0
            and p_avg_price < _to_decimal(p.min_price)
        )
        alert_stock = total_stock < ALERT_THRESHOLDS["min_stock"]

        product_rows.append(
            ProductDashboardRow(
                sku=p.sku,
                name=p.name,
                revenue=p_revenue,
                net_profit=p_net,
                margin=p_margin,
                drr=p_drr,
                avg_price=p_avg_price,
                min_price=p.min_price,
                total_stock=total_stock,
                alert_price=alert_price,
                alert_stock=alert_stock,
            )
        )

    # --- Alerts ---
    alerts: List[AlertItem] = []
    for p_row in product_rows:
        if p_row.revenue > 0:
            if p_row.margin < ALERT_THRESHOLDS["min_margin"]:
                alerts.append(
                    AlertItem(
                        type="warning",
                        text=f"{p_row.name}: маржа {p_row.margin:.1f}%",
                    )
                )
            if p_row.drr > ALERT_THRESHOLDS["max_drr"]:
                alerts.append(
                    AlertItem(
                        type="warning",
                        text=f"{p_row.name}: ДРР {p_row.drr:.1f}%",
                    )
                )
        if p_row.alert_price:
            alerts.append(
                AlertItem(
                    type="danger",
                    text=f"{p_row.name}: цена {p_row.avg_price:.0f}₽ ниже минимальной {p_row.min_price:.0f}₽",
                )
            )

    # --- Daily revenue trend by marketplace ---
    shop_mp = {s.id: s.marketplace.value for s in shops}
    daily_trend = []
    for day in trend_dates:
        day_sales = sales_by_day.get(day, [])
        day_returns = returns_by_day.get(day, [])
        day_rev = {mp: Decimal("0") for mp in MP_NAMES.keys()}
        for s in day_sales:
            mp = shop_mp.get(s.shop_id)
            if mp:
                day_rev[mp] += _gross_revenue(s)
        for r in day_returns:
            mp = shop_mp.get(r.shop_id)
            if mp:
                day_rev[mp] -= _gross_revenue(r)
        daily_trend.append(
            DailyTrendRow(
                date=day,
                wb_revenue=day_rev["wb"],
                ozon_revenue=day_rev["ozon"],
                ym_revenue=day_rev["ym"],
            )
        )

    # --- Real expense structure ---
    expense_structure = {
        "commission": Decimal("0"),
        "logistics": Decimal("0"),
        "storage": Decimal("0"),
        "ads": Decimal("0"),
        "returns": Decimal("0"),
        "other": Decimal("0"),
    }
    all_sales = list(sales) + list(returns)
    for s in all_sales:
        expense_structure["commission"] += max(_to_decimal(s.commission), Decimal("0"))
        expense_structure["logistics"] += max(_to_decimal(s.logistics), Decimal("0"))
        expense_structure["storage"] += max(_to_decimal(s.storage), Decimal("0"))
        expense_structure["ads"] += max(_to_decimal(s.advertising), Decimal("0"))
        expense_structure["returns"] += max(_to_decimal(s.returns), Decimal("0"))
        expense_structure["other"] += max(
            _to_decimal(s.insurance)
            + _to_decimal(s.acquiring)
            + _to_decimal(s.other),
            Decimal("0"),
        )

    return DashboardData(
        kpi=kpi,
        order_stats=OrderStats(
            orders_count=current_order_stats["orders_count"],
            average_check=current_order_stats["average_check"],
            average_profit_per_order=current_order_stats["average_profit_per_order"],
            profit_per_item=current_order_stats["profit_per_item"],
            returns_count=current_order_stats["returns_count"],
            return_rate=current_order_stats["return_rate"],
            avg_items_per_order=current_order_stats["avg_items_per_order"],
            orders_count_wow=orders_count_wow,
            average_check_wow=average_check_wow,
            average_profit_per_order_wow=average_profit_per_order_wow,
            profit_per_item_wow=profit_per_item_wow,
            returns_count_wow=returns_count_wow,
            return_rate_wow=return_rate_wow,
            avg_items_per_order_wow=avg_items_per_order_wow,
            orders_count_trend=orders_count_trend,
            average_check_trend=average_check_trend,
            average_profit_per_order_trend=average_profit_per_order_trend,
            profit_per_item_trend=profit_per_item_trend,
            returns_count_trend=returns_count_trend,
            avg_items_per_order_trend=avg_items_per_order_trend,
        ),
        alerts=alerts,
        marketplace_comparison=mp_comparison,
        unit_economics=unit_rows,
        products=product_rows,
        daily_trend=daily_trend,
        expense_structure=expense_structure,
    )
