from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime, timedelta, date
from typing import List, Dict, Optional
from decimal import Decimal

from fastapi import Query

from app.database import get_db
from app.models import (
    User,
    Shop,
    Sale,
    Stock,
    Advert,
    Marketplace,
    FinanceTransaction,
)
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
    CostCoverage,
)
from app.auth import get_current_user
from app.services.metrics import (
    to_decimal as _to_decimal,
    sale_expenses as _sale_expenses,
    gross_revenue as _gross_revenue,
    buyer_revenue as _buyer_revenue,
    actual_revenue as _actual_revenue,
    signed_finance_amount as _signed_finance_amount,
)
from app.services.sku_resolver import SkuResolver
from app.services.telegram_bot import WEEKLY_REPORT_DELAY_DAYS

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
    "max_drr": Decimal("12"),  # %
    "min_stock": 10,  # шт
}


def _shop_expenses(shop_id, mp: str, sales, returns, finance_transactions) -> Decimal:
    if mp == "ozon":
        return sum(
            -_signed_finance_amount(t)
            for t in finance_transactions
            if t.shop_id == shop_id
        )
    return sum(_sale_expenses(s) for s in sales if s.shop_id == shop_id) + sum(
        _sale_expenses(r) for r in returns if r.shop_id == shop_id
    )


def _shop_ads(shop_id, mp: str, sales, finance_transactions) -> Decimal:
    if mp == "ozon":
        return sum(
            -_signed_finance_amount(t)
            for t in finance_transactions
            if t.shop_id == shop_id and t.category == "advertising"
        )
    return sum(_to_decimal(s.advertising) for s in sales if s.shop_id == shop_id)


def _calc_period_kpis(
    sales, returns, finance_transactions, adverts, shops, resolver: SkuResolver
) -> Dict[str, Decimal]:
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
        _shop_ads(s.id, s.marketplace.value, sales, finance_transactions) for s in shops
    )
    total_gross = total_revenue - total_expenses
    total_cost = Decimal(0)
    cost_covered_revenue = Decimal(0)
    cost_missing_revenue = Decimal(0)
    sales_with_cost = 0
    for s in sales:
        product = resolver.resolve(s.shop_id, s.external_sku)
        if product is not None and _to_decimal(product.cost_price) > 0:
            total_cost += _to_decimal(product.cost_price) * (s.quantity or 0)
            cost_covered_revenue += _gross_revenue(s)
            sales_with_cost += 1
        else:
            cost_missing_revenue += _gross_revenue(s)
    total_net = total_gross - total_cost
    drr = (total_ads / total_revenue * 100) if total_revenue > 0 else Decimal(0)
    return {
        "revenue": total_revenue,
        "actual_revenue": total_actual_revenue,
        "gross": total_gross,
        "net": total_net,
        "drr": drr,
        "cost_covered_revenue": cost_covered_revenue,
        "cost_missing_revenue": cost_missing_revenue,
        "sales_with_cost": sales_with_cost,
    }


def _calc_order_stats(
    sales, returns, total_net: Optional[Decimal] = None
) -> Dict[str, Decimal]:
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

    average_check = (
        (total_actual_revenue / orders_count) if orders_count > 0 else Decimal(0)
    )
    average_profit_per_order = (
        (total_net / orders_count) if orders_count > 0 else Decimal(0)
    )
    profit_per_item = (total_net / total_items) if total_items > 0 else Decimal(0)
    return_rate = (
        Decimal(returns_count) / Decimal(total_orders_with_returns) * 100
        if total_orders_with_returns > 0
        else Decimal(0)
    )
    avg_items_per_order = (
        (Decimal(total_items) / Decimal(orders_count))
        if orders_count > 0
        else Decimal(0)
    )

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
    start_date: Optional[date] = Query(
        None,
        description="Start date (YYYY-MM-DD). Overrides period if provided together with end_date.",
    ),
    end_date: Optional[date] = Query(
        None,
        description="End date (YYYY-MM-DD). Overrides period if provided together with start_date.",
    ),
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
    elif period.startswith("m:"):
        # Single calendar month ("m:2026-08"). The frontend renders buttons
        # for the last three full months and shifts the set as a new month begins.
        try:
            y, m = map(int, period[2:].split("-"))
            start_dt = datetime(y, m, 1)
            next_month = datetime(y + 1, 1, 1) if m == 12 else datetime(y, m + 1, 1)
            end_dt = next_month - timedelta(microseconds=1)
        except ValueError:
            start_dt = (now - timedelta(days=30)).replace(
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

    # Product resolver: (shop_id, external_sku) -> canonical Product via
    # ProductShopMapping, with a fallback to a direct sku/canonical_sku match.
    resolver = await SkuResolver.create(db, current_user.id, shop_ids)

    # Get stocks
    stocks_result = await db.execute(select(Stock).where(Stock.shop_id.in_(shop_ids)))
    stocks = stocks_result.scalars().all()

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
                -_signed_finance_amount(t)
                for t in finance_transactions
                if t.shop_id == shop_id
            )
        return _shop_expenses_from_sales(shop_id)

    def _shop_ads(shop_id, mp: str) -> Decimal:
        if mp == "ozon":
            return sum(
                -_signed_finance_amount(t)
                for t in finance_transactions
                if t.shop_id == shop_id and t.category == "advertising"
            )
        return _shop_ads_from_sales(shop_id)

    # Calculate KPIs for the current period
    current = _calc_period_kpis(
        sales, returns, finance_transactions, adverts, shops, resolver
    )
    total_revenue = current["revenue"]
    total_actual_revenue = current["actual_revenue"]
    total_gross = current["gross"]
    total_net = current["net"]
    drr = current["drr"]

    official_ozon = None

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
        resolver,
    )

    def _wow_pct(cur: Decimal, prev: Decimal) -> float:
        return float(round((cur - prev) / abs(prev) * 100)) if prev != 0 else 0.0

    revenue_wow = _wow_pct(current["revenue"], previous["revenue"])
    gross_wow = _wow_pct(current["gross"], previous["gross"])
    net_wow = _wow_pct(current["net"], previous["net"])
    drr_wow = float(round(current["drr"] - previous["drr"]))

    # Order-level stats for current and previous periods
    current_order_stats = _calc_order_stats(sales, returns, current["net"])
    if official_ozon:
        current_order_stats.update(
            orders_count=official_ozon["orders"],
            average_check=(
                official_ozon["actual_revenue"] / official_ozon["orders"]
                if official_ozon["orders"]
                else Decimal(0)
            ),
            returns_count=official_ozon["returns"],
            avg_items_per_order=(
                Decimal(official_ozon["items"]) / official_ozon["orders"]
                if official_ozon["orders"]
                else Decimal(0)
            ),
            average_profit_per_order=(
                official_ozon["net_profit"] / official_ozon["orders"]
                if official_ozon["orders"]
                else Decimal(0)
            ),
            profit_per_item=(
                official_ozon["net_profit"] / official_ozon["items"]
                if official_ozon["items"]
                else Decimal(0)
            ),
            return_rate=(
                Decimal(official_ozon["returns"])
                / Decimal(official_ozon["orders"] + official_ozon["returns"])
                * 100
                if official_ozon["orders"] + official_ozon["returns"]
                else Decimal(0)
            ),
        )
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

        day_revenue = sum(_gross_revenue(s) for s in day_sales) - sum(
            _gross_revenue(r) for r in day_returns
        )
        day_actual_revenue = sum(_actual_revenue(s) for s in day_sales) - sum(
            _actual_revenue(r) for r in day_returns
        )
        day_expenses = Decimal("0")
        day_ads = Decimal("0")
        for shop in shops:
            if shop.marketplace == Marketplace.ozon:
                day_txs = [
                    t for t in finance_by_day.get(day, []) if t.shop_id == shop.id
                ]
                day_expenses += sum(-_signed_finance_amount(t) for t in day_txs)
                day_ads += sum(
                    -_signed_finance_amount(t)
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
        day_cost = Decimal(0)
        for s in day_sales:
            product = resolver.resolve(s.shop_id, s.external_sku)
            if product is not None:
                day_cost += _to_decimal(product.cost_price) * (s.quantity or 0)
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
            float(day_actual_revenue / day_orders_count)
            if day_orders_count > 0
            else 0.0
        )
        average_profit_per_order_trend.append(
            float(day_net / day_orders_count) if day_orders_count > 0 else 0.0
        )
        profit_per_item_trend.append(
            float(day_net / day_items) if day_items > 0 else 0.0
        )
        returns_count_trend.append(len(day_unique_returns))
        avg_items_per_order_trend.append(
            float(Decimal(day_items) / Decimal(day_orders_count))
            if day_orders_count > 0
            else 0.0
        )

    # Calculate KPI breakdown by marketplace
    kpi_by_marketplace: List[MarketplaceKPI] = []
    for shop in shops:
        mp = shop.marketplace.value
        mp_sales = [s for s in sales if s.shop_id == shop.id]
        mp_returns = [r for r in returns if r.shop_id == shop.id]
        mp_adverts = [a for a in adverts if a.shop_id == shop.id]

        mp_revenue = sum(_gross_revenue(s) for s in mp_sales) - sum(
            _gross_revenue(r) for r in mp_returns
        )
        mp_actual_revenue = sum(_actual_revenue(s) for s in mp_sales) - sum(
            _actual_revenue(r) for r in mp_returns
        )
        mp_expenses = _shop_expenses(shop.id, mp)
        mp_ads = _shop_ads(shop.id, mp)
        mp_gross = mp_revenue - mp_expenses
        mp_cost = Decimal(0)
        for s in mp_sales:
            product = resolver.resolve(s.shop_id, s.external_sku)
            if product is not None:
                mp_cost += _to_decimal(product.cost_price) * (s.quantity or 0)
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

    if official_ozon:
        kpi_by_marketplace = [
            MarketplaceKPI(
                marketplace=MP_NAMES["ozon"],
                revenue=official_ozon["revenue"],
                actual_revenue=official_ozon["actual_revenue"],
                expenses=official_ozon["expenses"],
                gross_profit=official_ozon["gross_profit"],
                net_profit=official_ozon["net_profit"],
                drr=Decimal(0),
            )
        ]

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
        cost = Decimal(0)
        for s in sales:
            if s.shop_id != shop.id:
                continue
            product = resolver.resolve(s.shop_id, s.external_sku)
            if product is not None:
                cost += _to_decimal(product.cost_price) * (s.quantity or 0)
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

    if official_ozon:
        mp_comparison = [
            MarketplaceComparison(
                marketplace=MP_NAMES["ozon"],
                revenue=official_ozon["revenue"],
                expenses=official_ozon["expenses"],
                gross_profit=official_ozon["gross_profit"],
                net_profit=official_ozon["net_profit"],
                net_margin=(
                    official_ozon["net_profit"] / official_ozon["revenue"] * 100
                    if official_ozon["revenue"]
                    else Decimal(0)
                ),
                drr=Decimal(0),
            )
        ]

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
        daily_revenue[key][day] = daily_revenue[key].get(
            day, Decimal(0)
        ) + _gross_revenue(s)
        daily_actual_revenue.setdefault(key, {})
        daily_actual_revenue[key][day] = daily_actual_revenue[key].get(
            day, Decimal(0)
        ) + _buyer_revenue(s)

    product_unit_map: Dict[str, dict] = {}
    for (external_sku, shop_id), s_sales in sku_sales.items():
        p = resolver.resolve(shop_id, external_sku)
        if p is None:
            continue
        shop = next((sh for sh in shops if sh.id == shop_id), None)
        if not shop:
            continue
        mp = shop.marketplace.value

        total_qty = sum(s.quantity or 0 for s in s_sales)
        total_revenue_sku = sum(_gross_revenue(s) for s in s_sales)
        gross_price = (total_revenue_sku / total_qty) if total_qty > 0 else Decimal(0)
        actual_price = (
            (sum(_buyer_revenue(s) for s in s_sales) / total_qty)
            if total_qty > 0
            else Decimal(0)
        )
        total_expenses_sku = sum(_sale_expenses(s) for s in s_sales)
        total_ads_sku = sum(_to_decimal(s.advertising) for s in s_sales)

        # Net/margin are based on the seller's gross price (what the marketplace
        # credits the seller, incl. marketplace-funded discounts/SPP), not on the
        # buyer-paid amount — otherwise marketplace-funded discounts look like a loss.
        expense_per_unit = (
            (total_expenses_sku / total_qty) if total_qty > 0 else Decimal(0)
        )
        net_per = gross_price - _to_decimal(p.cost_price) - expense_per_unit
        margin = (net_per / gross_price * 100) if gross_price > 0 else Decimal(0)
        drr_sku = (
            (total_ads_sku / total_revenue_sku * 100)
            if total_revenue_sku > 0
            else Decimal(0)
        )

        if p.sku not in product_unit_map:
            product_unit_map[p.sku] = {
                "sku": p.sku,
                "name": p.name,
                "cost": _to_decimal(p.cost_price),
                "rows": [],
            }

        trend = [
            int(
                daily_actual_revenue.get((external_sku, shop_id), {}).get(
                    day, Decimal(0)
                )
            )
            for day in trend_dates
        ]

        product_unit_map[p.sku]["rows"].append(
            UnitEconomicsMarketplaceRow(
                marketplace=MP_NAMES.get(mp, mp),
                sales=total_qty,
                gross_price=gross_price,
                actual_price=actual_price,
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

    if official_ozon:
        official_unit_rows = []
        for row in official_ozon["unit_economics"]:
            gross_price = row["gross_price"]
            profit_period = row["profit_period"]
            official_unit_rows.append(
                UnitEconomicsRow(
                    sku=row["sku"],
                    name=row["name"],
                    cost=row["cost"],
                    rows=[
                        UnitEconomicsMarketplaceRow(
                            marketplace=MP_NAMES["ozon"],
                            sales=row["items"],
                            gross_price=gross_price,
                            actual_price=row["actual_price"],
                            cost=row["cost"],
                            expense_per_unit=row["expense_per_unit"],
                            net_per_unit=row["profit_per_unit"],
                            margin=(
                                profit_period / (gross_price * row["items"]) * 100
                                if gross_price and row["items"]
                                else Decimal(0)
                            ),
                            drr=row["drr"],
                            trend=[0] * len(trend_dates),
                        )
                    ],
                )
            )
        unit_rows = sorted(
            official_unit_rows,
            key=lambda row: sum(item.net_per_unit * item.sales for item in row.rows),
            reverse=True,
        )

    # Product dashboard rows — sales/returns/stocks grouped by canonical product
    sales_by_product: Dict[str, list] = {}
    returns_by_product: Dict[str, list] = {}
    stocks_by_product: Dict[str, list] = {}
    for s in sales:
        product = resolver.resolve(s.shop_id, s.external_sku)
        if product is not None:
            sales_by_product.setdefault(str(product.id), []).append(s)
    for r in returns:
        product = resolver.resolve(r.shop_id, r.external_sku)
        if product is not None:
            returns_by_product.setdefault(str(product.id), []).append(r)
    for st in stocks:
        product = resolver.resolve(st.shop_id, st.external_sku)
        if product is not None:
            stocks_by_product.setdefault(str(product.id), []).append(st)

    product_rows: List[ProductDashboardRow] = []
    for p in resolver.products:
        p_sales = sales_by_product.get(str(p.id), [])
        p_returns = returns_by_product.get(str(p.id), [])
        p_revenue = sum(_gross_revenue(s) for s in p_sales) - sum(
            _gross_revenue(r) for r in p_returns
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

        p_stocks = stocks_by_product.get(str(p.id), [])
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

    if official_ozon:
        existing_products = {row.sku: row for row in product_rows}
        product_rows = []
        for row in official_ozon["unit_economics"]:
            existing = existing_products.get(row["sku"])
            product = resolver.resolve_sku(row["sku"])
            product_rows.append(
                ProductDashboardRow(
                    sku=row["sku"],
                    name=row["name"],
                    revenue=row["gross_price"] * row["items"],
                    net_profit=row["profit_period"],
                    margin=(
                        row["profit_period"] / (row["gross_price"] * row["items"]) * 100
                        if row["gross_price"] and row["items"]
                        else Decimal(0)
                    ),
                    drr=row["drr"],
                    avg_price=row["actual_price"],
                    min_price=product.min_price if product else Decimal(0),
                    total_stock=existing.total_stock if existing else 0,
                    alert_price=False,
                    alert_stock=existing.alert_stock if existing else False,
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
        if p_row.alert_stock and p_row.total_stock > 0:
            alerts.append(
                AlertItem(
                    type="warning",
                    text=f"{p_row.name}: остаток {p_row.total_stock} шт. ниже порога {ALERT_THRESHOLDS['min_stock']}",
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
            _to_decimal(s.insurance) + _to_decimal(s.acquiring) + _to_decimal(s.other),
            Decimal("0"),
        )

    # --- Cost coverage & profit reliability ---
    cost_coverage = None
    covered = current["cost_covered_revenue"]
    missing = current["cost_missing_revenue"]
    if sales or returns:
        coverage_total = covered + missing
        cost_coverage = CostCoverage(
            sales_total=len(sales),
            sales_with_cost=current["sales_with_cost"],
            revenue_covered=covered,
            revenue_uncovered=missing,
            coverage_percent=(
                float(covered / coverage_total * 100) if coverage_total > 0 else 100.0
            ),
        )
    # Marketplace finance data is confirmed with a delay; a period that ended
    # before the cutoff is considered confirmed, a fresher one is estimated.
    profit_status = (
        "confirmed"
        if end_dt < now - timedelta(days=WEEKLY_REPORT_DELAY_DAYS)
        else "estimated"
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
        cost_coverage=cost_coverage,
        profit_status=profit_status,
    )
