"""Sync service — fetches data from marketplace APIs and saves to DB."""

import logging
from datetime import datetime, timedelta, date

logger = logging.getLogger(__name__)
from typing import List, Dict, Any
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import selectinload

from app.adapters.base import AdapterFactory
from app.adapters.wildberries import RateLimitExceeded
from app.models import Shop, Sale, Stock, Advert, Product, ProductShopMapping, SyncLog, ShopBalance


class SyncService:
    """Service for syncing data from marketplaces to local DB."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def sync_shop(
        self,
        shop: Shop,
        days_back: int = 1,
        credentials: Dict[str, Any] | None = None,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
    ) -> Dict[str, Any]:
        """Sync all data for a single shop.

        credentials can be provided decrypted (e.g. from manual sync) without
        mutating the ORM shop.credentials field.
        date_from/date_to override days_back when provided (used by initial_sync).
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

        if date_from is None:
            date_from = (datetime.utcnow() - timedelta(days=days_back)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
        if date_to is None:
            date_to = datetime.utcnow()

        results = {
            "shop_id": str(shop.id),
            "marketplace": shop.marketplace.value,
            "status": "success",
            "orders": {"status": "success", "count": 0, "message": None},
            "stocks": {"status": "success", "count": 0, "message": None},
            "adverts": {"status": "success", "count": 0, "message": None},
            "prices": {"status": "success", "count": 0, "message": None},
            "finance": {"status": "success", "count": 0, "message": None},
            "balance": {"status": "success", "count": 0, "message": None},
        }

        # Sync current balance — isolated so a balance failure does not break the sync.
        try:
            balance_data = await adapter.get_balance()
            if balance_data and balance_data.get("is_supported", True):
                await self._upsert_balance(shop.id, balance_data)
                results["balance"] = {
                    "status": "success",
                    "count": 1,
                    "message": None,
                }
            elif balance_data:
                # Adapter explicitly says balance is not supported for this marketplace.
                await self._upsert_balance(shop.id, balance_data)
                results["balance"] = {
                    "status": "skipped",
                    "count": 0,
                    "message": "Balance not available for this marketplace",
                }
            else:
                results["balance"] = {
                    "status": "skipped",
                    "count": 0,
                    "message": "Balance not available for this marketplace",
                }
        except Exception as e:
            logger.warning("Failed to sync balance for shop %s: %s", shop.id, e)
            results["balance"] = {"status": "error", "count": 0, "message": str(e)}

        all_items = []

        try:
            # 1. Sync sales — prefer real-time orders over delayed analytics.
            try:
                if hasattr(adapter, 'get_orders'):
                    orders = await adapter.get_orders(date_from, date_to)
                    await self._save_orders_as_sales(shop.id, orders)
                    all_items.extend(orders)
                    results["orders"] = {"status": "success", "count": len(orders), "message": None}
                else:
                    sales = await adapter.get_sales(date_from, date_to)
                    await self._save_sales(shop.id, sales)
                    all_items.extend(sales)
                    results["orders"] = {"status": "success", "count": len(sales), "message": None}
            except RateLimitExceeded as e:
                results["orders"] = {"status": "rate_limited", "count": 0, "message": str(e)}
            except Exception as e:
                results["orders"] = {"status": "error", "count": 0, "message": str(e)}

            # 2. Sync stocks
            try:
                await self._clear_stocks(shop.id)
                stocks = await adapter.get_stocks()
                await self._save_stocks(shop.id, stocks)
                # Do not enrich products from stocks: WB warehouse_remains uses
                # nmId, which differs from the supplierArticle used by orders.
                results["stocks"] = {"status": "success", "count": len(stocks), "message": None}
            except RateLimitExceeded as e:
                results["stocks"] = {"status": "rate_limited", "count": 0, "message": str(e)}
            except Exception as e:
                results["stocks"] = {"status": "error", "count": 0, "message": str(e)}

            # 3. Sync adverts
            try:
                await self._clear_adverts(shop.id, date_from, date_to)
                adverts = await adapter.get_adverts(date_from, date_to)
                await self._save_adverts(shop.id, adverts)
                await self._distribute_advert_spend(shop.id, adverts, date_from, date_to)
                results["adverts"] = {"status": "success", "count": len(adverts), "message": None}
            except RateLimitExceeded as e:
                results["adverts"] = {"status": "rate_limited", "count": 0, "message": str(e)}
            except Exception as e:
                results["adverts"] = {"status": "error", "count": 0, "message": str(e)}

            # 4. Sync prices
            try:
                prices = await adapter.get_prices()
                all_items.extend(prices)
                results["prices"] = {"status": "success", "count": len(prices), "message": None}
            except RateLimitExceeded as e:
                results["prices"] = {"status": "rate_limited", "count": 0, "message": str(e)}
            except Exception as e:
                results["prices"] = {"status": "error", "count": 0, "message": str(e)}

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
                    results["finance"] = {"status": "success", "count": len(finance), "message": None}
                else:
                    results["finance"] = {"status": "success", "count": 0, "message": "No finance data"}
            except RateLimitExceeded as e:
                results["finance"] = {"status": "rate_limited", "count": 0, "message": str(e)}
            except Exception as e:
                results["finance"] = {"status": "error", "count": 0, "message": str(e)}

            shop.last_sync_at = datetime.utcnow()
            await self.db.commit()

        except Exception as e:
            await self.db.rollback()
            results["status"] = "error"
            results["message"] = str(e)

        # Persist sync log
        try:
            log = SyncLog(
                shop_id=shop.id,
                status=results["status"],
                sections={
                    k: v for k, v in results.items()
                    if k in ("orders", "stocks", "adverts", "prices", "finance", "balance")
                },
                message=results.get("message"),
            )
            self.db.add(log)
            await self.db.commit()
        except Exception as e:
            logger.warning("Failed to persist sync log: %s", e)
            await self.db.rollback()

        return results

    async def _upsert_balance(
        self,
        shop_id: Any,
        balance_data: Dict[str, Any],
    ):
        """Upsert ShopBalance record from adapter data."""
        from sqlalchemy.dialects.postgresql import insert

        payout_at = balance_data.get("payout_at")
        if payout_at and isinstance(payout_at, str):
            try:
                payout_at = datetime.fromisoformat(payout_at.replace("Z", "+00:00"))
            except (ValueError, TypeError):
                payout_at = None
        elif payout_at and isinstance(payout_at, datetime):
            payout_at = self._naive_dt(payout_at)

        is_supported = bool(balance_data.get("is_supported", True))
        stmt = insert(ShopBalance).values(
            shop_id=shop_id,
            balance=Decimal(str(balance_data.get("balance", 0) or 0)),
            payout_at=payout_at,
            currency=balance_data.get("currency", "RUB") or "RUB",
            is_supported=is_supported,
            updated_at=datetime.utcnow(),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=["shop_id"],
            set_={
                "balance": stmt.excluded.balance,
                "payout_at": stmt.excluded.payout_at,
                "currency": stmt.excluded.currency,
                "is_supported": stmt.excluded.is_supported,
                "updated_at": stmt.excluded.updated_at,
            },
        )
        await self.db.execute(stmt)

    def _naive_dt(self, value: datetime) -> datetime:
        return value.replace(tzinfo=None) if value and value.tzinfo else value

    async def _upsert_sales(self, shop_id, sales: List[Dict[str, Any]]):
        """Upsert sales to avoid duplicates across chunk syncs."""
        if not sales:
            return

        for item in sales:
            date = self._naive_dt(item["date"])
            external_sku = item["external_sku"]
            external_id = item.get("external_id") or ""
            quantity = int(item.get("quantity", 1) or 1)
            price = Decimal(str(item.get("price", 0) or 0))
            customer_price = Decimal(str(item.get("customer_price", price) or price))
            marketplace_discount = Decimal(str(item.get("marketplace_discount", 0) or 0))
            revenue = Decimal(str(item.get("revenue", price * quantity)))
            is_return = bool(item.get("is_return", False))

            stmt = insert(Sale).values(
                shop_id=shop_id,
                date=date,
                external_sku=external_sku,
                external_id=external_id,
                quantity=quantity,
                price=price,
                customer_price=customer_price,
                marketplace_discount=marketplace_discount,
                revenue=revenue,
                commission=Decimal(str(item.get("commission", 0) or 0)),
                logistics=Decimal(str(item.get("logistics", 0) or 0)),
                storage=Decimal(str(item.get("storage", 0) or 0)),
                advertising=Decimal(str(item.get("advertising", 0) or 0)),
                returns=Decimal(str(item.get("returns", 0) or 0)),
                insurance=Decimal(str(item.get("insurance", 0) or 0)),
                acquiring=Decimal(str(item.get("acquiring", 0) or 0)),
                other=Decimal(str(item.get("other", 0) or 0)),
                is_return=is_return,
            )
            stmt = stmt.on_conflict_do_update(
                index_elements=["shop_id", "external_id", "external_sku"],
                set_={
                    "date": stmt.excluded.date,
                    "quantity": stmt.excluded.quantity,
                    "price": stmt.excluded.price,
                    "customer_price": stmt.excluded.customer_price,
                    "marketplace_discount": stmt.excluded.marketplace_discount,
                    "revenue": stmt.excluded.revenue,
                    "commission": stmt.excluded.commission,
                    "logistics": stmt.excluded.logistics,
                    "storage": stmt.excluded.storage,
                    "advertising": stmt.excluded.advertising,
                    "returns": stmt.excluded.returns,
                    "insurance": stmt.excluded.insurance,
                    "acquiring": stmt.excluded.acquiring,
                    "other": stmt.excluded.other,
                    "is_return": stmt.excluded.is_return,
                },
            )
            await self.db.execute(stmt)

    async def _save_sales(self, shop_id, sales: List[Dict[str, Any]]):
        await self._upsert_sales(shop_id, sales)

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
        """Convert real-time orders to sales format and upsert."""
        sales = []
        for item in orders:
            status = item.get("status", "").upper()
            is_return = status in (
                "CANCELLED", "CANCELLED_BY_CUSTOMER", "RETURNED", "PARTIALLY_RETURNED"
            )
            sales.append({
                "date": item["date"],
                "external_sku": item["external_sku"],
                "external_id": item.get("external_id"),
                "quantity": item.get("quantity", 1),
                "price": item["price"],
                "customer_price": item.get("customer_price", item["price"]),
                "marketplace_discount": item.get("marketplace_discount", Decimal("0")),
                "revenue": item.get("revenue", item["price"] * item.get("quantity", 1)),
                "commission": Decimal(str(item.get("commission", 0) or 0)),
                "logistics": Decimal(str(item.get("logistics", 0) or 0)),
                "storage": Decimal(str(item.get("storage", 0) or 0)),
                "advertising": Decimal(str(item.get("advertising", 0) or 0)),
                "returns": Decimal(str(item.get("returns", 0) or 0)),
                "insurance": Decimal(str(item.get("insurance", 0) or 0)),
                "acquiring": Decimal(str(item.get("acquiring", 0) or 0)),
                "other": Decimal(str(item.get("other", 0) or 0)),
                "is_return": is_return,
            })
        await self._upsert_sales(shop_id, sales)

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

    async def _distribute_advert_spend(
        self, shop_id, adverts: List[Dict[str, Any]], date_from: datetime, date_to: datetime
    ):
        """Distribute advert spend onto matching Sale.advertising for DRR.

        WB advert stats arrive per nmId/advertId per day. We try to match by
        external_sku first, then fall back to all sales of the day weighted by
        revenue. This mirrors the unallocated-advertising logic in
        _update_finance_data so the dashboard DRR reflects actual ad costs.
        """
        if not adverts:
            return

        from collections import defaultdict

        # Aggregate spend per day.
        spend_by_day: Dict[date, Decimal] = defaultdict(Decimal)
        spend_by_sku_day: Dict[Tuple[str, date], Decimal] = defaultdict(Decimal)
        for item in adverts:
            day = self._naive_dt(item["date"]).date()
            spend = Decimal(str(item.get("spend", 0) or 0))
            spend_by_day[day] += spend
            spend_by_sku_day[(str(item.get("external_sku", "")), day)] += spend

        # Reset advertising for the period before redistributing.
        await self.db.execute(
            update(Sale)
            .where(
                Sale.shop_id == shop_id,
                Sale.date >= date_from,
                Sale.date <= date_to,
            )
            .values(advertising=Decimal("0"))
        )

        for day, total_spend in spend_by_day.items():
            start_dt = datetime.combine(day, datetime.min.time())
            end_dt = datetime.combine(day, datetime.max.time())
            result = await self.db.execute(
                select(Sale).where(
                    Sale.shop_id == shop_id,
                    Sale.date >= start_dt,
                    Sale.date <= end_dt,
                )
            )
            sales = result.scalars().all()
            if not sales:
                continue

            # Try to allocate by external_sku first.
            matched_sku_spend = Decimal("0")
            for sale in sales:
                sku_spend = spend_by_sku_day.get((sale.external_sku, day), Decimal("0"))
                if sku_spend > 0:
                    sale.advertising += sku_spend
                    matched_sku_spend += sku_spend

            unallocated = total_spend - matched_sku_spend
            if unallocated <= 0:
                continue

            # Distribute remainder proportionally by revenue.
            total_revenue = sum((sale.revenue or Decimal(0)) for sale in sales)
            if total_revenue > 0:
                for sale in sales:
                    weight = (sale.revenue or Decimal(0)) / total_revenue
                    sale.advertising += unallocated * weight
            else:
                per_sale = unallocated / len(sales)
                for sale in sales:
                    sale.advertising += per_sale

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
        """Distribute finance-level expenses across matching sales rows.

        Ozon finance data is keyed by posting_number (``external_id``). Yandex
        Market finance data is keyed by shop SKU (``external_sku``). When a
        posting contains several products we split amounts proportionally by SKU
        revenue; for SKU-level reports the amount is attached directly to all
        matching sales rows.

        Expense columns are reset before each sync so repeated runs do not double
        count the same transactions.
        """
        if not finance:
            return

        await self.db.execute(
            update(Sale)
            .where(
                Sale.shop_id == shop_id,
                Sale.date >= date_from,
                Sale.date <= date_to,
            )
            .values(
                commission=Decimal("0"),
                logistics=Decimal("0"),
                storage=Decimal("0"),
                advertising=Decimal("0"),
                returns=Decimal("0"),
                insurance=Decimal("0"),
                acquiring=Decimal("0"),
                other=Decimal("0"),
            )
        )

        EXPENSE_KEYS = ["commission", "logistics", "storage", "advertising", "returns", "insurance", "acquiring", "other"]
        unallocated: Dict[str, Decimal] = {k: Decimal("0") for k in EXPENSE_KEYS}

        for item in finance:
            posting_number = item.get("external_id") or item.get("posting_number")
            sku = item.get("external_sku")

            if posting_number:
                result = await self.db.execute(
                    select(Sale).where(
                        Sale.shop_id == shop_id,
                        Sale.external_id == posting_number,
                        Sale.date >= date_from,
                        Sale.date <= date_to,
                    )
                )
                sales = result.scalars().all()
            elif sku:
                # Yandex Market finance report is keyed by shop SKU.
                result = await self.db.execute(
                    select(Sale).where(
                        Sale.shop_id == shop_id,
                        Sale.external_sku == sku,
                        Sale.date >= date_from,
                        Sale.date <= date_to,
                    )
                )
                sales = result.scalars().all()
            else:
                continue

            if not sales:
                # Finance reports sometimes contain IDs that do not match any sale
                # (different date cutoffs, corrections, posting shorthands, etc.).
                # Keep all unmatched expenses aside and distribute them across all
                # sales so nothing is lost.
                for key in EXPENSE_KEYS:
                    unallocated[key] += Decimal(str(item.get(key, 0) or 0))
                continue

            total_revenue = sum((sale.revenue or Decimal(0)) for sale in sales)
            if total_revenue <= 0:
                # No revenue to proportion against; put everything on the first row.
                weights = {id(sales[0]): Decimal(1)} if sales else {}
            else:
                weights = {
                    id(sale): (sale.revenue or Decimal(0)) / total_revenue
                    for sale in sales
                }

            for sale in sales:
                weight = weights.get(id(sale), Decimal(0))
                sale.commission += Decimal(str(item.get("commission", 0) or 0)) * weight
                sale.logistics += Decimal(str(item.get("logistics", 0) or 0)) * weight
                sale.storage += Decimal(str(item.get("storage", 0) or 0)) * weight
                sale.advertising += Decimal(str(item.get("advertising", 0) or 0)) * weight
                sale.returns += Decimal(str(item.get("returns", 0) or 0)) * weight
                sale.insurance += Decimal(str(item.get("insurance", 0) or 0)) * weight
                sale.acquiring += Decimal(str(item.get("acquiring", 0) or 0)) * weight
                sale.other += Decimal(str(item.get("other", 0) or 0)) * weight

        # Distribute any expenses that could not be matched to specific sales.
        for key, total in unallocated.items():
            if total == 0:
                continue
            result = await self.db.execute(
                select(Sale).where(
                    Sale.shop_id == shop_id,
                    Sale.date >= date_from,
                    Sale.date <= date_to,
                )
            )
            all_sales = result.scalars().all()
            total_revenue = sum((sale.revenue or Decimal(0)) for sale in all_sales)
            if total_revenue > 0:
                for sale in all_sales:
                    weight = (sale.revenue or Decimal(0)) / total_revenue
                    current = Decimal(getattr(sale, key))
                    setattr(sale, key, current + total * weight)
            elif all_sales:
                # No revenue but rows exist; split evenly to avoid losing the expense.
                per_sale = total / len(all_sales)
                for sale in all_sales:
                    current = Decimal(getattr(sale, key))
                    setattr(sale, key, current + per_sale)

    async def initial_sync(
        self,
        shop: Shop,
        days_back: int = 365,
        credentials: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Fetch historical sales in API-safe chunks.

        YM: max 30 days per request.
        Ozon: max 90 days per request for regular sellers.
        WB: statistics API is heavily rate-limited, use 30-day chunks.
        """
        import asyncio

        mp = shop.marketplace.value
        chunk_days = 30 if mp in ("ym", "wb") else 90

        overall = {
            "shop_id": str(shop.id),
            "marketplace": mp,
            "status": "success",
            "chunks": 0,
            "sales": 0,
            "errors": [],
        }

        end = datetime.utcnow()
        start = (end - timedelta(days=days_back)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

        current = start
        while current < end:
            chunk_end = min(current + timedelta(days=chunk_days), end)
            try:
                result = await self.sync_shop(
                    shop,
                    credentials=credentials,
                    date_from=current,
                    date_to=chunk_end,
                )
                overall["chunks"] += 1
                overall["sales"] += result.get("sales", 0)
                if result.get("status") != "success":
                    overall["errors"].append(result.get("message"))
            except Exception as e:
                overall["errors"].append(str(e))
                # Cool down before the next chunk to avoid rate-limit cascades
                await asyncio.sleep(120)
            finally:
                await self.db.rollback()

            current = chunk_end

        if overall["errors"]:
            overall["status"] = "partial" if overall["sales"] > 0 else "error"
            overall["message"] = "; ".join(overall["errors"])

        return overall

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
