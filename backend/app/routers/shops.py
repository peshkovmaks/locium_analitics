from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from uuid import UUID

from app.database import get_db
from app.models import Shop, User
from app.schemas import ShopCreate, ShopOut
from app.auth import get_current_user, get_current_admin
from app.utils.encryption import encrypt_dict, decrypt_dict

router = APIRouter()


@router.get("/shops", response_model=List[ShopOut])
async def list_shops(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    result = await db.execute(select(Shop).where(Shop.user_id == current_user.id))
    return result.scalars().all()


@router.post("/shops", response_model=ShopOut)
async def create_shop(
    data: ShopCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    encrypted_creds = encrypt_dict(data.credentials)

    shop = Shop(
        user_id=current_user.id,
        marketplace=data.marketplace,
        name=data.name,
        credentials=encrypted_creds,
    )
    db.add(shop)
    await db.commit()
    await db.refresh(shop)
    return shop


@router.put("/shops/{shop_id}/toggle-sync")
async def toggle_sync(
    shop_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    result = await db.execute(
        select(Shop).where(Shop.id == shop_id, Shop.user_id == current_user.id)
    )
    shop = result.scalar_one_or_none()
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")

    shop.sync_enabled = not shop.sync_enabled
    await db.commit()
    await db.refresh(shop)
    return shop


@router.post("/shops/{shop_id}/sync")
async def manual_sync(
    shop_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_admin)
):
    from app.services.sync_service import SyncService

    result = await db.execute(
        select(Shop).where(Shop.id == shop_id, Shop.user_id == current_user.id)
    )
    shop = result.scalar_one_or_none()
    if not shop:
        raise HTTPException(status_code=404, detail="Shop not found")

    shop.credentials = decrypt_dict(shop.credentials)

    sync_service = SyncService(db)
    result = await sync_service.sync_shop(shop, days_back=1)
    return result
