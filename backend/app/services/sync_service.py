"""Sync service — fetches data from marketplace APIs and saves to DB."""

from datetime import datetime, timedelta
from typing import List, Dict, Any
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload

from app.adapters.base import AdapterFactory
from app.models import Shop, Sale, Stock, Advert, Product, ProductShopMapping


class SyncService:
    """Service for syncing data from marketplaces to local DB."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def sync_shop(
        self, shop: Shop, days_back: int = 1, credentials: Dict[str, Any] | None = None
    ) -> Dict[str, Any]:
        """Sync all data for a single shop.

        credentials can be provided decrypted (e.g. from manual sync) without
        mutating the ORM shop.credentials field.
        """
        adapter = AdapterFactory.create(
            shop.marketplace.value,
            str(shop.id),
            credentials if credentials is not None else shop.credentials,
        )

        if not await adapter.authenticate():
            return {
                "shop_id": str(shop.id),
                "status": "error",
                "message": "Authentication failed",
            }

        date_from = (datetime.utcnow() - timedelta(days=days_back)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
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

        all_items = []

        try:
            # 1. Sync sales — prefer real-time orders over delayed analytics
            # Remove previous data for the same period to avoid duplicates.
            await self._clear_sales(shop.id, date_from, date_to)
            if hasattr(adapter, 'get_orders'):
                orders = await adapter.get_orders(date_from, date_to)
                await self._save_orders_as_sales(shop.id, orders)
                all_items.extend(orders)
                results["sales"] = len(orders)
            else:
                sales = await adapter.get_sales(date_from, date_to)
                await self._save_sales(shop.id, sales)
                all_items.extend(sales)
                results["sales"] = len(sales)

            # 2. Sync stocks
            try:
                await self._clear_stocks(shop.id)
                stocks = await adapter.get_stocks()
                await self._save_stocks(shop.id, stocks)
                all_items.extend(stocks)
                results["stocks"] = len(stocks)
            except Exception:
                results["stocks"] = 0

            # 3. Sync adverts
            try:
                await self._clear_adverts(shop.id, date_from, date_to)
                adverts = await adapter.get_adverts(date_from, date_to)
                await self._save_adverts(shop.id, adverts)
                results["adverts"] = len(adverts)
            except Exception:
                results["adverts"] = 0

            # 4. Sync prices
            try:
                prices = await adapter.get_prices()
                all_items.extend(prices)
                results["prices"] = len(prices)
            except Exception:
                results["prices"] = 0

            # Enrich product names when adapter supports it
            offer_ids = [
                str(item.get("external_sku", ""))
                for item in all_items
                if item.get("external_sku")
            ]
            names = {}
            if offer_ids and hasattr(adapter, "get_product_info"):
                try:
                    names = await adapter.get_product_info(offer_ids)
                except Exception:
                    names = {}

            await self._ensure_products(shop, all_items, names=names)

            # 5. Finance report
            try:
                finance = await adapter.get_finance_report(date_from, date_to)
                if finance:
                    await self._update_finance_data(shop.id, finance, date_from, date_to)
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

    def _naive_dt(self, value: datetime) -> datetime:
        return value.replace(tzinfo=None) if value and value.tzinfo else value

    async def _save_sales(self, shop_id, sales: List[Dict[str, Any]]):
        for item in sales:
            sale = Sale(
                shop_id=shop_id,
                date=self._naive_dt(item["date"]),

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

    async def _clear_sales(self, shop_id, date_from: datetime, date_to: datetime):
        await self.db.execute(
            delete(Sale).where(
                Sale.shop_id == shop_id,
                Sale.date >= date_from,
                Sale.date <= date_to + timedelta(days=1),
            )
        )

    async def _clear_stocks(self, shop_id):
        await self.db.execute(delete(Stock).where(Stock.shop_id == shop_id))

    async def _clear_adverts(self, shop_id, date_from: datetime, date_to: datetime):
        await self.db.execute(
            delete(Advert).where(
                Advert.shop_id == shop_id,
                Advert.date >= date_from,
                Advert.date <= date_to + timedelta(days=1),
            )
        )

    async def _save_orders_as_sales(self, shop_id, orders: List[Dict[str, Any]]):
        """Convert real-time orders to sales format."""
        for item in orders:
            status = item.get("status", "").upper()
            is_return = status in ("CANCELLED", "CANCELLED_BY_CUSTOMER", "RETURNED", "PARTIALLY_RETURNED")

            sale = Sale(
                shop_id=shop_id,
                date=self._naive_dt(item["date"]),
                external_sku=item["external_sku"],
                external_id=item.get("external_id"),
                quantity=item["quantity"],
                price=item["price"],
                revenue=item["price"] * item["quantity"],
                commission=Decimal(0),
                logistics=Decimal(0),
                storage=Decimal(0),
                advertising=Decimal(0),
                returns=Decimal(0),
                other=Decimal(0),
                is_return=is_return,
            )
            self.db.add(sale)

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

    async def _ensure_products(self, shop: Shop, items, names: Dict[str, str] | None = None):
        """Create Product records for new SKUs with shop mappings.

        Prefers real names from sales/orders over bare SKUs coming from prices.
        """
        names = names or {}
        for item in items:
            external_sku = item.get("external_sku")
            if not external_sku:
                continue

            # Use a real name if available; prefer fetched names over item-level fallbacks.
            fetched_name = names.get(external_sku)
            item_name = item.get("name")
            if item_name and item_name != external_sku:
                raw_name = item_name
            elif fetched_name and fetched_name != external_sku:
                raw_name = fetched_name
            else:
                raw_name = None
            has_real_name = bool(raw_name)
            fallback_name = external_sku or "Unknown"

            # 1. Check existing mapping and eagerly load related product
            mapping_result = await self.db.execute(
                select(ProductShopMapping)
                .where(
                    ProductShopMapping.shop_id == shop.id,
                    ProductShopMapping.external_sku == external_sku,
                )
                .options(selectinload(ProductShopMapping.product))
            )
            mapping = mapping_result.scalar_one_or_none()

            if mapping:
                if has_real_name and mapping.product and mapping.product.name != raw_name:
                    mapping.product.name = raw_name
                continue

            # 2. Check existing product by canonical_sku or sku
            product_result = await self.db.execute(
                select(Product).where(
                    Product.user_id == shop.user_id,
                    (Product.canonical_sku == external_sku) | (Product.sku == external_sku),
                )
            )
            product = product_result.scalar_one_or_none()

            if not product:
                product = Product(
                    user_id=shop.user_id,
                    sku=external_sku,
                    canonical_sku=external_sku,
                    name=raw_name if has_real_name else fallback_name,
                    cost_price=Decimal(0),
                    min_price=Decimal(0),
                )
                self.db.add(product)
                await self.db.flush()

            # 3. Create mapping
            new_mapping = ProductShopMapping(
                product_id=product.id,
                shop_id=shop.id,
                external_sku=external_sku,
            )
            self.db.add(new_mapping)

    async def _update_finance_data(
        self, shop_id, finance: List[Dict[str, Any]], date_from: datetime, date_to: datetime
    ):
        for item in finance:
            result = await self.db.execute(
                select(Sale).where(
                    Sale.shop_id == shop_id,
                    Sale.external_sku == item["external_sku"],
                    Sale.date >= date_from,
                    Sale.date <= date_to,
                )
            )
            sales = result.scalars().all()
            for sale in sales:
                sale.commission = item.get("commission", sale.commission)
                sale.logistics = item.get("logistics", sale.logistics)
                sale.storage = item.get("storage", sale.storage)
                sale.returns = item.get("returns", sale.returns)
                sale.other = item.get("other", sale.other)

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
