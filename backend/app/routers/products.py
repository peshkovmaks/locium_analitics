from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from typing import List
from decimal import Decimal

from app.database import get_db
from app.models import Product, User, ProductShopMapping
from app.schemas import ProductOut, ProductCostUpdate
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
