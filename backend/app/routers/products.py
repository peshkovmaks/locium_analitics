from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from decimal import Decimal

from app.database import get_db
from app.models import Product, User, Shop
from app.schemas import ProductOut, ProductCostUpdate
from app.auth import get_current_user

router = APIRouter()


@router.get("/products", response_model=List[ProductOut])
async def list_products(
    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)
):
    result = await db.execute(select(Product).where(Product.user_id == current_user.id))
    return result.scalars().all()


@router.put("/products/{sku}/cost", response_model=ProductOut)
async def update_product_cost(
    sku: str,
    cost_data: ProductCostUpdate,  # ← ТЕПЕРЬ ТУТ МОДЕЛЬ
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Product).where(Product.sku == sku, Product.user_id == current_user.id)
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found")

    # Теперь cost_price гарантированно есть и это число ≥ 0
    product.cost_price = Decimal(str(cost_data.cost_price))

    await db.commit()
    await db.refresh(product)
    return product
