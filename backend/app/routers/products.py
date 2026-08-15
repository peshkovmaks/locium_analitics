from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, update
from typing import List
from decimal import Decimal

from app.database import get_db
from app.models import Product, User, ProductShopMapping
from app.schemas import ProductOut, ProductCostUpdate, ProductMerge
from app.auth import get_current_user

router = APIRouter()


@router.get("/products", response_model=List[ProductOut])
async def list_products(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
):
    # Берём товары, у которых canonical_sku заполнен (уникальные)
    result = await db.execute(
        select(Product)
        .where(Product.user_id == current_user.id, Product.canonical_sku.isnot(None))
        .order_by(Product.name)
    )
    return result.scalars().all()


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
