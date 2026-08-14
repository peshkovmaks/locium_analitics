from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime, timedelta
from typing import List
from decimal import Decimal

from app.database import get_db
from app.models import User, Shop, Product, Sale, Stock, Advert, Marketplace
from app.schemas import (
    DashboardData,
    KPIData,
    AlertItem,
    MarketplaceComparison,
    UnitEconomicsRow,
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
    if period == "today":
        start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "7d":
        start_date = now - timedelta(days=7)
    else:
        start_date = now - timedelta(days=30)

    # Get sales
    sales_result = await db.execute(
        select(Sale).where(
            Sale.shop_id.in_(shop_ids), Sale.date >= start_date, Sale.is_return == False
        )
    )
    sales = sales_result.scalars().all()

    # Get returns
    returns_result = await db.execute(
        select(Sale).where(
            Sale.shop_id.in_(shop_ids), Sale.date >= start_date, Sale.is_return == True
        )
    )
    returns = returns_result.scalars().all()

    # Get adverts
    adverts_result = await db.execute(
        select(Advert).where(Advert.shop_id.in_(shop_ids), Advert.date >= start_date)
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

    # Calculate KPIs
    total_revenue = sum(s.revenue for s in sales) - sum(r.revenue for r in returns)
    total_expenses = sum(
        s.commission + s.logistics + s.storage + s.advertising + s.returns + s.other
        for s in sales
    )
    total_ads = sum(a.spend for a in adverts)
    total_gross = total_revenue - total_expenses

    # Calculate cost
    total_cost = Decimal(0)
    for s in sales:
        sku = s.external_sku
        if sku in products:
            total_cost += products[sku].cost_price * s.quantity

    total_net = total_gross - total_cost
    drr = (total_ads / total_revenue * 100) if total_revenue > 0 else Decimal(0)

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
        rev = sum(s.revenue for s in sales if s.shop_id == shop.id)
        exp = sum(
            s.commission + s.logistics + s.storage + s.advertising + s.returns + s.other
            for s in sales
            if s.shop_id == shop.id
        )
        gross = rev - exp
        cost = sum(
            products[s.external_sku].cost_price * s.quantity
            for s in sales
            if s.shop_id == shop.id and s.external_sku in products
        )
        net = gross - cost
        ads = sum(a.spend for a in adverts if a.shop_id == shop.id)
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

    # Unit economics
    unit_rows: List[UnitEconomicsRow] = []
    for s in sales:
        if s.external_sku not in products:
            continue
        p = products[s.external_sku]
        shop = next((sh for sh in shops if sh.id == s.shop_id), None)
        if not shop:
            continue
        mp = shop.marketplace.value
        r = EXPENSE_RATIOS.get(mp, EXPENSE_RATIOS["wb"])
        exp_per_unit = s.price * Decimal(sum(r.values()))
        net_per = s.price - p.cost_price - exp_per_unit
        unit_rows.append(
            UnitEconomicsRow(
                sku=p.sku,
                name=p.name,
                marketplace=MP_NAMES.get(mp, mp),
                price=s.price,
                cost=p.cost_price,
                expense_per_unit=exp_per_unit,
                net_per_unit=net_per,
                sales=s.quantity,
                total_net=net_per * s.quantity,
            )
        )
    unit_rows.sort(key=lambda x: x.net_per_unit, reverse=True)

    # Product dashboard rows
    product_rows: List[ProductDashboardRow] = []
    for p in products.values():
        p_sales = [s for s in sales if s.external_sku == p.sku]
        p_revenue = sum(s.revenue for s in p_sales)
        p_expenses = sum(
            s.commission + s.logistics + s.storage + s.advertising + s.returns + s.other
            for s in p_sales
        )
        p_ads = sum(a.spend for a in adverts if a.external_sku == p.sku)
        p_gross = p_revenue - p_expenses
        p_cost = p.cost_price * sum(s.quantity for s in p_sales)
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
