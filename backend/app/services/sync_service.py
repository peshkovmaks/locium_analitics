"""Sync service — fetches data from marketplace APIs and saves to DB."""

from datetime import datetime, timedelta
from typing import List, Dict, Any
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.adapters.base import AdapterFactory
from app.models import Shop, Sale, Stock, Advert, Product


class SyncService:
    """Service for syncing data from marketplaces to local DB."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def sync_shop(self, shop: Shop, days_back: int = 1) -> Dict[str, Any]:
        """Sync all data for a single shop."""
        adapter = AdapterFactory.create(
            shop.marketplace.value,
            str(shop.id),
            shop.credentials,
        )

        if not await adapter.authenticate():
            return {
                "shop_id": str(shop.id),
                "status": "error",
                "message": "Authentication failed",
            }

        date_from = datetime.utcnow() - timedelta(days=days_back)
        date_to = datetime.utcnow()

        results = {
            "shop_id": str(shop.id),
            "marketplace": shop.marketplace.value,
            "status": "success",
            "sales": 0,
            "stocks": 0,
            "adverts": 0,
            "prices": 0,
        }

        try:
            # 1. Sync sales
            sales = await adapter.get_sales(date_from, date_to)
            await self._save_sales(shop.id, sales)
            await self._ensure_products(shop.id, sales)
            results["sales"] = len(sales)

            # 2. Sync stocks
            stocks = await adapter.get_stocks()
            await self._save_stocks(shop.id, stocks)
            await self._ensure_products(shop.id, stocks)
            results["stocks"] = len(stocks)

            # 3. Sync adverts
            adverts = await adapter.get_adverts(date_from, date_to)
            await self._save_adverts(shop.id, adverts)
            results["adverts"] = len(adverts)

            # 4. Sync prices
            prices = await adapter.get_prices()
            results["prices"] = len(prices)

            # 5. Finance report
            try:
                finance = await adapter.get_finance_report(date_from, date_to)
                if finance:
                    await self._update_finance_data(shop.id, finance)
                    results["finance_records"] = len(finance)
            except Exception as e:
                results["finance_error"] = str(e)

            shop.last_sync_at = datetime.utcnow()
            await self.db.commit()

        except Exception as e:
            await self.db.rollback()
            results["status"] = "error"
            results["message"] = str(e)

        return results

    async def _ensure_products(self, shop_id, items):
        """Create Product records for new SKUs."""
        result = await self.db.execute(select(Shop).where(Shop.id == shop_id))
        shop = result.scalar_one_or_none()
        if not shop:
            return

        for item in items:
            sku = item.get("external_sku")
            name = item.get("name") or sku or "Unknown"
            if not sku:
                continue

            existing = await self.db.execute(
                select(Product).where(
                    Product.user_id == shop.user_id, Product.sku == sku
                )
            )
            if existing.scalar_one_or_none():
                continue

            product = Product(
                user_id=shop.user_id,
                sku=sku,
                name=name,
                cost_price=Decimal(0),
                min_price=Decimal(0),
            )
            self.db.add(product)
        await self.db.commit()

    async def _save_sales(self, shop_id, sales: List[Dict[str, Any]]):
        for item in sales:
            sale = Sale(
                shop_id=shop_id,
                date=item["date"],
                external_sku=item["external_sku"],
                external_id=item.get("external_id"),
                quantity=item["quantity"],
                price=item["price"],
                revenue=item["revenue"],
                commission=item.get("commission", Decimal(0)),
                logistics=item.get("logistics", Decimal(0)),
                storage=item.get("storage", Decimal(0)),
                advertising=item.get("advertising", Decimal(0)),
                returns=item.get("returns", Decimal(0)),
                other=item.get("other", Decimal(0)),
                is_return=item.get("is_return", False),
            )
            self.db.add(sale)
        await self.db.commit()

    async def _save_stocks(self, shop_id, stocks: List[Dict[str, Any]]):
        for item in stocks:
            stock = Stock(
                shop_id=shop_id,
                date=datetime.utcnow(),
                external_sku=item["external_sku"],
                external_id=item.get("external_id"),
                warehouse=item.get("warehouse", "Unknown"),
                quantity=item["quantity"],
                in_way=item.get("in_way", 0),
            )
            self.db.add(stock)
        await self.db.commit()

    async def _save_adverts(self, shop_id, adverts: List[Dict[str, Any]]):
        for item in adverts:
            advert = Advert(
                shop_id=shop_id,
                date=item["date"],
                campaign_id=item.get("campaign_id"),
                external_sku=item["external_sku"],
                views=item.get("views", 0),
                clicks=item.get("clicks", 0),
                ctr=item.get("ctr", Decimal(0)),
                cpc=item.get("cpc", Decimal(0)),
                spend=item.get("spend", Decimal(0)),
                orders=item.get("orders", 0),
                cr=item.get("cr", Decimal(0)),
            )
            self.db.add(advert)
        await self.db.commit()

    async def _update_finance_data(self, shop_id, finance: List[Dict[str, Any]]):
        for item in finance:
            result = await self.db.execute(
                select(Sale).where(
                    Sale.shop_id == shop_id,
                    Sale.external_sku == item["external_sku"],
                )
            )
            sale = result.scalar_one_or_none()
            if sale:
                sale.commission = item.get("commission", sale.commission)
                sale.logistics = item.get("logistics", sale.logistics)
                sale.storage = item.get("storage", sale.storage)
                sale.returns = item.get("returns", sale.returns)
                sale.other = item.get("other", sale.other)
                await self.db.commit()

    async def sync_all_active_shops(self, days_back: int = 1) -> List[Dict[str, Any]]:
        result = await self.db.execute(
            select(Shop).where(Shop.is_active == True, Shop.sync_enabled == True)
        )
        shops = result.scalars().all()

        results = []
        for shop in shops:
            shop_result = await self.sync_shop(shop, days_back)
            results.append(shop_result)

        return results
