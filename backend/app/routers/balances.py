"""Balances API."""

from typing import List
from uuid import UUID
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models import Shop, ShopBalance
from app.schemas import BalanceOut
from app.auth import get_current_user

router = APIRouter()


@router.get("/", response_model=List[BalanceOut])
async def list_balances(
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Return current balances for all active shops of the current user."""
    stmt = (
        select(Shop, ShopBalance)
        .join(ShopBalance, ShopBalance.shop_id == Shop.id, isouter=True)
        .where(Shop.user_id == current_user.id, Shop.is_active == True)
        .order_by(Shop.created_at)
    )
    result = await db.execute(stmt)
    rows = result.all()

    balances = []
    for shop, balance in rows:
        balances.append({
            "shop_id": shop.id,
            "marketplace": shop.marketplace,
            "shop_name": shop.name,
            "balance": balance.balance if balance else Decimal("0"),
            "payout_at": balance.payout_at if balance else None,
            "currency": balance.currency if balance else "RUB",
            "updated_at": balance.updated_at if balance else None,
        })
    return balances
