from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update
from sqlalchemy.orm import selectinload
from typing import List
from decimal import Decimal

from app.database import get_db
from app.models import Product, User, ProductShopMapping, Sale, Shop, PriceHistory
from app.schemas import (
    ProductOut,
    ProductCostUpdate,
    ProductMerge,
    PriceHistoryPoint,
    PriceRecommendation,
)
from app.auth import get_current_user
from app.services.sku_resolver import SkuResolver
from app.routers.dashboard import ALERT_THRESHOLDS

router = APIRouter()

# Период продаж для расчёта средней комиссии/логистики и текущей цены.
PRICE_RECOMMENDATION_DAYS = 30
# Зазор, в пределах которого текущая цена считается совпадающей с рекомендацией.
PRICE_KEEP_TOLERANCE = Decimal("0.03")
# Целевая маржа — та же константа, что порог маржинального алерта в dashboard.
TARGET_MARGIN = ALERT_THRESHOLDS["min_margin"] / Decimal("100")


async def _compute_price_recommendations(
    db: AsyncSession, user_id, products: List[Product]
) -> dict:
    """Рекомендация цены по формуле из плана:

    минимальная цена = себестоимость / (1 - комиссия - логистика - целевая маржа),

    где комиссия+логистика — средний процент от выручки по продажам товара
    за последние PRICE_RECOMMENDATION_DAYS дней (через SkuResolver).
    """
    if not products:
        return {}

    date_from = datetime.utcnow() - timedelta(days=PRICE_RECOMMENDATION_DAYS)
    resolver = await SkuResolver.create(db, user_id)
    sales_result = await db.execute(
        select(Sale)
        .join(Shop, Sale.shop_id == Shop.id)
        .where(Shop.user_id == user_id, Sale.date >= date_from, Sale.is_return == False)
    )

    # product_id -> [revenue, fees, weighted price sum, price qty]
    stats: dict = {}
    for s in sales_result.scalars().all():
        product = resolver.resolve(s.shop_id, s.external_sku)
        if product is None:
            continue
        st = stats.setdefault(
            str(product.id),
            {
                "revenue": Decimal("0"),
                "fees": Decimal("0"),
                "price_sum": Decimal("0"),
                "price_qty": 0,
            },
        )
        revenue = Decimal(str(s.revenue or 0))
        st["revenue"] += revenue
        st["fees"] += Decimal(str(s.commission or 0)) + Decimal(
            str(s.logistics or 0)
        )
        qty = int(s.quantity or 0)
        price = Decimal(str(s.customer_price or 0)) or Decimal(str(s.price or 0))
        if qty > 0 and price > 0:
            st["price_sum"] += price * qty
            st["price_qty"] += qty

    recommendations = {}
    for p in products:
        cost = Decimal(str(p.cost_price or 0))
        st = stats.get(str(p.id))
        current = (
            (st["price_sum"] / st["price_qty"]).quantize(Decimal("0.01"))
            if st and st["price_qty"] > 0
            else None
        )
        if cost <= 0 or not st or st["revenue"] <= 0:
            recommendations[str(p.id)] = None
            continue

        fee_rate = st["fees"] / st["revenue"]
        denominator = Decimal("1") - fee_rate - TARGET_MARGIN
        if denominator <= 0:
            recommendations[str(p.id)] = PriceRecommendation(
                recommended_price=None, min_price=current, action="keep"
            )
            continue

        recommended = (cost / denominator).quantize(Decimal("0.01"))
        if current is None:
            action = "keep"
        elif current < recommended * (1 - PRICE_KEEP_TOLERANCE):
            action = "raise"
        elif current > recommended * (1 + PRICE_KEEP_TOLERANCE):
            action = "lower"
        else:
            action = "keep"
        recommendations[str(p.id)] = PriceRecommendation(
            recommended_price=recommended, min_price=current, action=action
        )
    return recommendations


@router.get("/products", response_model=List[ProductOut])
async def list_products(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
):
    # Берём товары, у которых canonical_sku заполнен (уникальные)
    result = await db.execute(
        select(Product)
        .where(Product.user_id == current_user.id, Product.canonical_sku.isnot(None))
        .order_by(Product.name)
        .options(selectinload(Product.mappings))
    )
    products = result.scalars().all()

    # Продажи по external_sku за всё время (для определения товаров без продаж)
    sales_result = await db.execute(
        select(
            Sale.external_sku,
            func.sum(Sale.quantity).label("qty"),
            func.sum(Sale.revenue).label("rev"),
        )
        .join(Shop)
        .where(Shop.user_id == current_user.id, Sale.is_return == False)
        .group_by(Sale.external_sku)
    )
    sales_by_sku: dict = {}
    for row in sales_result.all():
        sales_by_sku[row.external_sku] = {
            "qty": int(row.qty or 0),
            "rev": Decimal(str(row.rev or 0)),
        }

    recommendations = await _compute_price_recommendations(
        db, current_user.id, list(products)
    )

    for p in products:
        total_qty = 0
        total_rev = Decimal(0)
        for m in p.mappings:
            info = sales_by_sku.get(m.external_sku)
            if info:
                total_qty += info["qty"]
                total_rev += info["rev"]
        p.sales_count = total_qty
        p.total_revenue = total_rev
        p.price_recommendation = recommendations.get(str(p.id))

    return products


@router.get(
    "/products/{product_id}/price-history", response_model=List[PriceHistoryPoint]
)
async def get_price_history(
    product_id: UUID,
    date_from: Optional[datetime] = Query(default=None),
    date_to: Optional[datetime] = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Product).where(
            Product.id == product_id, Product.user_id == current_user.id
        )
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    query = (
        select(PriceHistory.price, PriceHistory.created_at)
        .where(PriceHistory.product_id == product.id)
        .order_by(PriceHistory.created_at)
    )
    if date_from is not None:
        query = query.where(PriceHistory.created_at >= date_from)
    if date_to is not None:
        query = query.where(PriceHistory.created_at <= date_to)

    rows = (await db.execute(query)).all()
    return [PriceHistoryPoint(price=row.price, created_at=row.created_at) for row in rows]


@router.put("/products/{sku}/cost", response_model=ProductOut)
async def update_product_cost(
    sku: str,
    cost_data: ProductCostUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Ищем по canonical_sku или sku
    result = await db.execute(
        select(Product).where(
            Product.user_id == current_user.id,
            (Product.canonical_sku == sku) | (Product.sku == sku),
        )
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    product.cost_price = Decimal(str(cost_data.cost_price))

    await db.commit()
    await db.refresh(product)
    return product


@router.post("/products/merge", response_model=ProductOut)
async def merge_products(
    data: ProductMerge,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Merge several products into one canonical_sku.

    All source ProductShopMappings are reassigned to the target product,
    and source products are removed.
    """
    # 1. Find target product
    target_result = await db.execute(
        select(Product).where(
            Product.user_id == current_user.id,
            (Product.canonical_sku == data.target_sku) | (Product.sku == data.target_sku),
        )
    )
    target = target_result.scalar_one_or_none()
    if not target:
        raise HTTPException(status_code=404, detail="Target product not found")

    # 2. Find source products (excluding target)
    source_result = await db.execute(
        select(Product).where(
            Product.user_id == current_user.id,
            Product.id != target.id,
            (Product.canonical_sku.in_(data.source_skus)) | (Product.sku.in_(data.source_skus)),
        )
    )
    sources = source_result.scalars().all()
    if not sources:
        raise HTTPException(status_code=404, detail="No source products found")

    source_ids = {source.id for source in sources}

    # 3. Reassign mappings to target
    await db.execute(
        update(ProductShopMapping)
        .where(ProductShopMapping.product_id.in_(source_ids))
        .values(product_id=target.id)
    )

    # 4. Sync canonical_sku on target and all mappings
    target.canonical_sku = data.target_sku
    for source in sources:
        await db.delete(source)

    await db.commit()
    await db.refresh(target)
    return target
