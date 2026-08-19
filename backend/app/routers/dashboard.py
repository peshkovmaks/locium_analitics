from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime, timedelta, date
from typing import List, Dict, Optional
from decimal import Decimal

from fastapi import Query

from app.database import get_db
from app.models import User, Shop, Product, Sale, Stock, Advert, Marketplace
from app.schemas import (
    DashboardData,
    KPIData,
    MarketplaceKPI,
    AlertItem,
    MarketplaceComparison,
    UnitEconomicsRow,
    UnitEconomicsMarketplaceRow,
    ProductDashboardRow,
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
            _to_decimal(s.commission)
            + _to_decimal(s.logistics)
            + _to_decimal(s.storage)
            + _to_decimal(s.advertising)
            + _to_decimal(s.returns)
            + _to_decimal(s.insurance)
            + _to_decimal(s.acquiring)
            + _to_decimal(s.other)
        )

    # Calculate KPIs
    total_revenue = sum(_to_decimal(s.revenue) for s in sales) - sum(_to_decimal(r.revenue) for r in returns)
    total_expenses = sum(_sale_expenses(s) for s in sales)
    total_ads = sum(_to_decimal(a.spend) for a in adverts)
    total_gross = total_revenue - total_expenses

    # Calculate cost
    total_cost = Decimal(0)
    for s in sales:
        sku = s.external_sku
        if sku in products:
            total_cost += _to_decimal(products[sku].cost_price) * (s.quantity or 0)

    total_net = total_gross - total_cost
    drr = (total_ads / total_revenue * 100) if total_revenue > 0 else Decimal(0)

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

    revenue_trend = []
    gross_trend = []
    net_trend = []
    drr_trend = []
    for day in trend_dates:
        day_sales = sales_by_day.get(day, [])
        day_returns = returns_by_day.get(day, [])
        day_adverts = adverts_by_day.get(day, [])

        day_revenue = sum(_to_decimal(s.revenue) for s in day_sales) - sum(_to_decimal(r.revenue) for r in day_returns)
        day_expenses = sum(_sale_expenses(s) for s in day_sales)
        day_gross = day_revenue - day_expenses
        day_cost = sum(
            _to_decimal(products[s.external_sku].cost_price) * (s.quantity or 0)
            for s in day_sales
            if s.external_sku in products
        )
        day_net = day_gross - day_cost
        day_ads = sum(_to_decimal(a.spend) for a in day_adverts)
        day_drr = float(day_ads / day_revenue * 100) if day_revenue > 0 else 0.0

        revenue_trend.append(float(day_revenue))
        gross_trend.append(float(day_gross))
        net_trend.append(float(day_net))
        drr_trend.append(day_drr)

    # Calculate KPI breakdown by marketplace
    kpi_by_marketplace: List[MarketplaceKPI] = []
    for shop in shops:
        mp = shop.marketplace.value
        mp_sales = [s for s in sales if s.shop_id == shop.id]
        mp_returns = [r for r in returns if r.shop_id == shop.id]
        mp_adverts = [a for a in adverts if a.shop_id == shop.id]

        mp_revenue = sum(_to_decimal(s.revenue) for s in mp_sales) - sum(_to_decimal(r.revenue) for r in mp_returns)
        mp_expenses = sum(_sale_expenses(s) for s in mp_sales)
        mp_ads = sum(_to_decimal(a.spend) for a in mp_adverts)
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
                gross_profit=mp_gross,
                net_profit=mp_net,
                drr=mp_drr,
            )
        )

    # Mock WoW (in production: compare with previous period)
    kpi = KPIData(
        revenue=total_revenue,
        gross_profit=total_gross,
        net_profit=total_net,
        drr=drr,
        revenue_wow=12.0,
        gross_wow=5.0,
        net_wow=-3.0,
        drr_wow=-1.2,
        by_marketplace=kpi_by_marketplace,
        revenue_trend=revenue_trend,
        gross_trend=gross_trend,
        net_trend=net_trend,
        drr_trend=drr_trend,
    )

    # Alerts
    alerts: List[AlertItem] = []
    for s in stocks:
        if s.quantity < 10:
            alerts.append(
                AlertItem(
                    type="warning", text=f"{s.external_sku}: остаток {s.quantity} шт"
                )
            )

    # Marketplace comparison
    mp_comparison: List[MarketplaceComparison] = []
    for shop in shops:
        mp = shop.marketplace.value
        rev = sum(_to_decimal(s.revenue) for s in sales if s.shop_id == shop.id)
        exp = sum(_sale_expenses(s) for s in sales if s.shop_id == shop.id)
        gross = rev - exp
        cost = sum(
            _to_decimal(products[s.external_sku].cost_price) * (s.quantity or 0)
            for s in sales
            if s.shop_id == shop.id and s.external_sku in products
        )
        net = gross - cost
        ads = sum(_to_decimal(a.spend) for a in adverts if a.shop_id == shop.id)
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
    trend_dates = []
    current_day = start_dt.date()
    end_day = end_dt.date()
    while current_day <= end_day:
        trend_dates.append(current_day)
        current_day += timedelta(days=1)

    daily_revenue: Dict[tuple, Dict[date, Decimal]] = {}
    for s in sales:
        key = (s.external_sku, s.shop_id)
        day = s.date.date() if s.date else None
        if day is None:
            continue
        daily_revenue.setdefault(key, {})
        daily_revenue[key][day] = daily_revenue[key].get(day, Decimal(0)) + _to_decimal(s.revenue)

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
        total_expenses_sku = sum(_sale_expenses(s) for s in s_sales)
        total_ads_sku = sum(_to_decimal(s.advertising) for s in s_sales)

        avg_price = (total_revenue_sku / total_qty) if total_qty > 0 else Decimal(0)
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
            int(daily_revenue.get((external_sku, shop_id), {}).get(day, Decimal(0)))
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
        p_revenue = sum(_to_decimal(s.revenue) for s in p_sales)
        p_expenses = sum(_sale_expenses(s) for s in p_sales)
        p_ads = sum(_to_decimal(a.spend) for a in adverts if a.external_sku == p.sku)
        p_gross = p_revenue - p_expenses
        p_cost = _to_decimal(p.cost_price) * sum(s.quantity or 0 for s in p_sales)
        p_net = p_gross - p_cost
        p_margin = (p_net / p_revenue * 100) if p_revenue > 0 else Decimal(0)
        p_drr = (p_ads / p_revenue * 100) if p_revenue > 0 else Decimal(0)

        p_stocks = [st for st in stocks if st.external_sku == p.sku]
        total_stock = sum(st.quantity for st in p_stocks)

        product_rows.append(
            ProductDashboardRow(
                sku=p.sku,
                name=p.name,
                revenue=p_revenue,
                net_profit=p_net,
                margin=p_margin,
                drr=p_drr,
                avg_price=Decimal(0),  # Will be calculated from actual prices
                min_price=p.min_price,
                total_stock=total_stock,
                alert_price=False,  # Will check against actual prices
                alert_stock=total_stock < 20,
            )
        )

    return DashboardData(
        kpi=kpi,
        alerts=alerts,
        marketplace_comparison=mp_comparison,
        unit_economics=unit_rows,
        products=product_rows,
    )
