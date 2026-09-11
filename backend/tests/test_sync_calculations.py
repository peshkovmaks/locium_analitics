"""Tests for sync-service calculations: finance distribution, advertising
spend, marketplace discounts, multi-SKU orders, product merging and returns.

Run:
    cd backend
    python -m pytest tests/test_sync_calculations.py -v
"""

import uuid
from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, update, delete, func

from app.models import (
    Marketplace,
    Product,
    ProductShopMapping,
    Sale,
    Shop,
    Stock,
    SyncLog,
    FinanceTransaction,
)
from app.services.sync_service import SyncService


@pytest.fixture
async def ozon_shop(db_session, test_user) -> Shop:
    shop = Shop(
        id=uuid.uuid4(),
        user_id=test_user.id,
        marketplace=Marketplace.ozon,
        name="Ozon Calc Shop",
        credentials={},
        is_active=True,
        sync_enabled=True,
        created_at=datetime.utcnow(),
    )
    db_session.add(shop)
    await db_session.commit()
    await db_session.refresh(shop)
    return shop


def make_order(
    sku: str,
    price,
    quantity: int = 1,
    order_id: str | None = None,
    date: datetime | None = None,
    **extra,
) -> dict:
    order = {
        "date": date or datetime.utcnow(),
        "external_sku": sku,
        "external_id": order_id or f"order-{sku}",
        "quantity": quantity,
        "price": Decimal(str(price)),
    }
    order.update(extra)
    return order


async def get_sales(db_session, shop_id) -> list[Sale]:
    result = await db_session.execute(
        select(Sale).where(Sale.shop_id == shop_id).order_by(Sale.external_sku)
    )
    return result.scalars().all()


class TestSaveOrdersAsSales:
    """Order → Sale conversion: discounts, returns, defaults."""

    async def test_customer_price_and_marketplace_discount_saved(
        self, db_session, ozon_shop
    ):
        svc = SyncService(db_session)
        await svc._save_orders_as_sales(
            ozon_shop.id,
            [
                make_order(
                    "SKU-D",
                    1000,
                    quantity=2,
                    customer_price=800,
                    marketplace_discount=200,
                )
            ],
        )
        await db_session.commit()

        sale = (await get_sales(db_session, ozon_shop.id))[0]
        assert sale.price == Decimal("1000")
        assert sale.customer_price == Decimal("800")
        assert sale.marketplace_discount == Decimal("200")
        # revenue defaults to price * quantity (pre-discount base).
        assert sale.revenue == Decimal("2000")
        assert sale.is_return is False

    async def test_defaults_customer_price_to_price(self, db_session, ozon_shop):
        svc = SyncService(db_session)
        await svc._save_orders_as_sales(
            ozon_shop.id, [make_order("SKU-P", 500)]
        )
        await db_session.commit()

        sale = (await get_sales(db_session, ozon_shop.id))[0]
        assert sale.customer_price == Decimal("500")
        assert sale.marketplace_discount == Decimal("0")
        assert sale.revenue == Decimal("500")

    @pytest.mark.parametrize(
        "status",
        ["CANCELLED", "CANCELLED_BY_CUSTOMER", "RETURNED", "PARTIALLY_RETURNED"],
    )
    async def test_return_statuses_marked_as_return(
        self, db_session, ozon_shop, status
    ):
        svc = SyncService(db_session)
        await svc._save_orders_as_sales(
            ozon_shop.id, [make_order(f"SKU-R-{status}", 100, status=status)]
        )
        await db_session.commit()

        sale = (await get_sales(db_session, ozon_shop.id))[0]
        assert sale.is_return is True

    async def test_explicit_is_return_flag_wins(self, db_session, ozon_shop):
        svc = SyncService(db_session)
        await svc._save_orders_as_sales(
            ozon_shop.id,
            [make_order("SKU-F", 100, status="delivered", is_return=True)],
        )
        await db_session.commit()

        sale = (await get_sales(db_session, ozon_shop.id))[0]
        assert sale.is_return is True


class TestUpdateFinanceData:
    """Finance expense distribution across sales."""

    async def test_multi_sku_order_split_by_revenue(self, db_session, ozon_shop):
        svc = SyncService(db_session)
        day = datetime.utcnow() - timedelta(hours=1)
        await svc._upsert_sales(
            ozon_shop.id,
            [
                {
                    "date": day,
                    "external_sku": "SKU-A",
                    "external_id": "order-1",
                    "quantity": 1,
                    "price": Decimal("300"),
                    "revenue": Decimal("300"),
                },
                {
                    "date": day,
                    "external_sku": "SKU-B",
                    "external_id": "order-1",
                    "quantity": 1,
                    "price": Decimal("700"),
                    "revenue": Decimal("700"),
                },
            ],
        )
        await db_session.commit()

        date_from = day - timedelta(days=1)
        date_to = datetime.utcnow()
        await svc._update_finance_data(
            ozon_shop.id,
            [{"external_id": "order-1", "commission": Decimal("100")}],
            date_from,
            date_to,
        )
        await db_session.commit()

        sales = {s.external_sku: s for s in await get_sales(db_session, ozon_shop.id)}
        # 300/1000 and 700/1000 revenue weights.
        assert sales["SKU-A"].commission == Decimal("30")
        assert sales["SKU-B"].commission == Decimal("70")

    async def test_normalized_negative_amount_becomes_positive_expense(
        self, db_session, ozon_shop
    ):
        """Normalized transactions use the marketplace convention where
        expenses arrive as negative amounts; Sale columns stay positive."""
        svc = SyncService(db_session)
        day = datetime.utcnow() - timedelta(hours=1)
        await svc._upsert_sales(
            ozon_shop.id,
            [
                {
                    "date": day,
                    "external_sku": "SKU-N",
                    "external_id": "order-n",
                    "quantity": 1,
                    "price": Decimal("500"),
                    "revenue": Decimal("500"),
                }
            ],
        )
        await db_session.commit()

        date_from = day - timedelta(days=1)
        date_to = datetime.utcnow()
        await svc._update_finance_data(
            ozon_shop.id,
            [
                {
                    "posting_number": "order-n",
                    "category": "commission",
                    "amount": Decimal("-120"),
                }
            ],
            date_from,
            date_to,
        )
        await db_session.commit()

        sale = (await get_sales(db_session, ozon_shop.id))[0]
        assert sale.commission == Decimal("120")

    async def test_normalized_positive_credit_clamped_to_zero(
        self, db_session, ozon_shop
    ):
        """A positive (crediting) amount must not inflate the expense column."""
        svc = SyncService(db_session)
        day = datetime.utcnow() - timedelta(hours=1)
        await svc._upsert_sales(
            ozon_shop.id,
            [
                {
                    "date": day,
                    "external_sku": "SKU-C",
                    "external_id": "order-c",
                    "quantity": 1,
                    "price": Decimal("500"),
                    "revenue": Decimal("500"),
                }
            ],
        )
        await db_session.commit()

        date_from = day - timedelta(days=1)
        date_to = datetime.utcnow()
        await svc._update_finance_data(
            ozon_shop.id,
            [
                {
                    "posting_number": "order-c",
                    "category": "commission",
                    "amount": Decimal("80"),
                }
            ],
            date_from,
            date_to,
        )
        await db_session.commit()

        sale = (await get_sales(db_session, ozon_shop.id))[0]
        assert sale.commission == Decimal("0")

    async def test_unmatched_finance_row_distributed_across_sales(
        self, db_session, ozon_shop
    ):
        svc = SyncService(db_session)
        day = datetime.utcnow() - timedelta(hours=1)
        await svc._upsert_sales(
            ozon_shop.id,
            [
                {
                    "date": day,
                    "external_sku": "SKU-U1",
                    "external_id": "order-u1",
                    "quantity": 1,
                    "price": Decimal("100"),
                    "revenue": Decimal("100"),
                },
                {
                    "date": day,
                    "external_sku": "SKU-U2",
                    "external_id": "order-u2",
                    "quantity": 1,
                    "price": Decimal("300"),
                    "revenue": Decimal("300"),
                },
            ],
        )
        await db_session.commit()

        date_from = day - timedelta(days=1)
        date_to = datetime.utcnow()
        # Posting number that matches nothing must not be lost.
        await svc._update_finance_data(
            ozon_shop.id,
            [{"external_id": "unknown-posting", "logistics": Decimal("80")}],
            date_from,
            date_to,
        )
        await db_session.commit()

        sales = {s.external_sku: s for s in await get_sales(db_session, ozon_shop.id)}
        assert sales["SKU-U1"].logistics == Decimal("20")
        assert sales["SKU-U2"].logistics == Decimal("60")

    async def test_expenses_reset_before_redistribution(self, db_session, ozon_shop):
        svc = SyncService(db_session)
        day = datetime.utcnow() - timedelta(hours=1)
        await svc._upsert_sales(
            ozon_shop.id,
            [
                {
                    "date": day,
                    "external_sku": "SKU-T",
                    "external_id": "order-t",
                    "quantity": 1,
                    "price": Decimal("100"),
                    "revenue": Decimal("100"),
                }
            ],
        )
        await db_session.commit()

        date_from = day - timedelta(days=1)
        date_to = datetime.utcnow()
        await svc._update_finance_data(
            ozon_shop.id,
            [{"external_id": "order-t", "commission": Decimal("10")}],
            date_from,
            date_to,
        )
        await db_session.commit()

        # Second run with a different amount must not accumulate.
        await svc._update_finance_data(
            ozon_shop.id,
            [{"external_id": "order-t", "commission": Decimal("40")}],
            date_from,
            date_to,
        )
        await db_session.commit()

        sale = (await get_sales(db_session, ozon_shop.id))[0]
        assert sale.commission == Decimal("40")


class TestSaveFinanceTransactions:
    """Signed finance transactions keep returns/storno signs."""

    async def test_negative_amount_preserved(self, db_session, ozon_shop):
        svc = SyncService(db_session)
        date_from = datetime.utcnow() - timedelta(days=1)
        date_to = datetime.utcnow()
        await svc._save_finance_transactions(
            ozon_shop.id,
            ozon_shop.marketplace,
            [
                {
                    "operation_date": date_from,
                    "posting_number": "order-x",
                    "external_sku": "SKU-X",
                    "operation_type": "returns",
                    "category": "returns",
                    "amount": Decimal("-250"),
                }
            ],
            date_from,
            date_to,
        )
        await db_session.commit()

        result = await db_session.execute(select(FinanceTransaction))
        tx = result.scalars().one()
        assert tx.amount == Decimal("-250")
        assert tx.category == "returns"

    async def test_idempotent_replace_for_period(self, db_session, ozon_shop):
        svc = SyncService(db_session)
        date_from = datetime.utcnow() - timedelta(days=1)
        date_to = datetime.utcnow()

        async def run(amount):
            await svc._save_finance_transactions(
                ozon_shop.id,
                ozon_shop.marketplace,
                [
                    {
                        "operation_date": date_from,
                        "category": "commission",
                        "amount": Decimal(str(amount)),
                    }
                ],
                date_from,
                date_to,
            )
            await db_session.commit()

        await run(100)
        await run(200)

        result = await db_session.execute(select(FinanceTransaction))
        txs = result.scalars().all()
        assert len(txs) == 1
        assert txs[0].amount == Decimal("200")


class TestDistributeAdvertSpend:
    async def _seed_two_skus(self, db_session, shop_id):
        day = datetime.utcnow() - timedelta(hours=1)
        svc = SyncService(db_session)
        await svc._upsert_sales(
            shop_id,
            [
                {
                    "date": day,
                    "external_sku": "SKU-A",
                    "external_id": "order-a",
                    "quantity": 1,
                    "price": Decimal("1000"),
                    "revenue": Decimal("1000"),
                },
                {
                    "date": day,
                    "external_sku": "SKU-B",
                    "external_id": "order-b",
                    "quantity": 1,
                    "price": Decimal("1000"),
                    "revenue": Decimal("1000"),
                },
            ],
        )
        await db_session.commit()
        return day

    async def test_sku_match_then_revenue_weighted_remainder(
        self, db_session, ozon_shop
    ):
        day = await self._seed_two_skus(db_session, ozon_shop.id)
        svc = SyncService(db_session)
        date_from = day - timedelta(days=1)
        date_to = datetime.utcnow()

        await svc._distribute_advert_spend(
            ozon_shop.id,
            [
                {
                    "date": day,
                    "external_sku": "SKU-A",
                    "spend": Decimal("100"),
                },
                {
                    "date": day,
                    "external_sku": "SKU-UNKNOWN",
                    "spend": Decimal("100"),
                },
            ],
            date_from,
            date_to,
        )
        await db_session.commit()

        sales = {s.external_sku: s for s in await get_sales(db_session, ozon_shop.id)}
        # SKU-A gets its direct 100 plus half of the unallocated 100.
        assert sales["SKU-A"].advertising == Decimal("150")
        assert sales["SKU-B"].advertising == Decimal("50")

    async def test_redistribution_resets_previous_values(self, db_session, ozon_shop):
        day = await self._seed_two_skus(db_session, ozon_shop.id)
        svc = SyncService(db_session)
        date_from = day - timedelta(days=1)
        date_to = datetime.utcnow()

        adverts = [{"date": day, "external_sku": "SKU-A", "spend": Decimal("100")}]
        await svc._distribute_advert_spend(
            ozon_shop.id, adverts, date_from, date_to
        )
        await db_session.commit()

        await svc._distribute_advert_spend(
            ozon_shop.id, adverts, date_from, date_to
        )
        await db_session.commit()

        sales = {s.external_sku: s for s in await get_sales(db_session, ozon_shop.id)}
        # 100 direct on A, nothing left unallocated — B stays at zero.
        assert sales["SKU-A"].advertising == Decimal("100")
        assert sales["SKU-B"].advertising == Decimal("0")


class TestProductMerge:
    """Merging products via ProductShopMapping at the model/service level

    (mirrors POST /products/merge in routers/products.py without calling it).
    """

    async def test_merge_reassigns_mappings_and_aggregates_sales(
        self, db_session, ozon_shop, test_user
    ):
        target = Product(
            id=uuid.uuid4(),
            user_id=test_user.id,
            sku="SKU-A",
            canonical_sku="SKU-A",
            name="Product A",
        )
        source = Product(
            id=uuid.uuid4(),
            user_id=test_user.id,
            sku="SKU-B",
            canonical_sku="SKU-B",
            name="Product B",
        )
        db_session.add_all([target, source])
        await db_session.flush()
        db_session.add_all(
            [
                ProductShopMapping(
                    product_id=target.id,
                    shop_id=ozon_shop.id,
                    external_sku="SKU-A",
                ),
                ProductShopMapping(
                    product_id=source.id,
                    shop_id=ozon_shop.id,
                    external_sku="SKU-B",
                ),
            ]
        )

        day = datetime.utcnow() - timedelta(hours=1)
        svc = SyncService(db_session)
        await svc._upsert_sales(
            ozon_shop.id,
            [
                {
                    "date": day,
                    "external_sku": "SKU-A",
                    "external_id": "order-a",
                    "quantity": 1,
                    "price": Decimal("1000"),
                    "revenue": Decimal("1000"),
                },
                {
                    "date": day,
                    "external_sku": "SKU-B",
                    "external_id": "order-b",
                    "quantity": 1,
                    "price": Decimal("500"),
                    "revenue": Decimal("500"),
                },
            ],
        )
        await db_session.commit()

        # Merge: reassign source mappings to target, drop the source product.
        await db_session.execute(
            update(ProductShopMapping)
            .where(ProductShopMapping.product_id == source.id)
            .values(product_id=target.id)
        )
        await db_session.execute(delete(Product).where(Product.id == source.id))
        await db_session.commit()

        result = await db_session.execute(
            select(func.coalesce(func.sum(Sale.revenue), 0))
            .join(
                ProductShopMapping,
                ProductShopMapping.external_sku == Sale.external_sku,
            )
            .where(
                ProductShopMapping.product_id == target.id,
                Sale.shop_id == ozon_shop.id,
            )
        )
        assert result.scalar() == Decimal("1500")

        result = await db_session.execute(
            select(Product).where(Product.id == source.id)
        )
        assert result.scalar_one_or_none() is None


class FakeAdapter:
    """Controllable adapter stub for sync_shop-level tests."""

    def __init__(
        self,
        orders=None,
        stocks=None,
        adverts=None,
        prices=None,
        finance=None,
        stocks_error=None,
        finance_error=None,
        authenticated=True,
    ):
        self._orders = orders or []
        self._stocks = stocks
        self._adverts = adverts or []
        self._prices = prices or []
        self._finance = finance or []
        self._stocks_error = stocks_error
        self._finance_error = finance_error
        self._authenticated = authenticated

    async def authenticate(self):
        return self._authenticated

    async def get_orders(self, date_from, date_to):
        return self._orders

    async def get_stocks(self):
        if self._stocks_error:
            raise self._stocks_error
        return self._stocks

    async def get_adverts(self, date_from, date_to):
        return self._adverts

    async def get_prices(self):
        return self._prices

    async def get_product_info(self, offer_ids):
        return {}

    async def get_finance_report(self, date_from, date_to):
        if self._finance_error:
            raise self._finance_error
        return self._finance

    async def get_balance(self):
        return None


def _install_fake_adapter(monkeypatch, adapter: FakeAdapter):
    monkeypatch.setattr(
        "app.services.sync_service.AdapterFactory.create",
        lambda *args, **kwargs: adapter,
    )


async def _seed_stock(db_session, shop_id, sku="OLD-SKU", quantity=5) -> Stock:
    stock = Stock(
        shop_id=shop_id,
        date=datetime.utcnow(),
        external_sku=sku,
        warehouse="w1",
        quantity=quantity,
    )
    db_session.add(stock)
    await db_session.commit()
    return stock


async def _shop_stocks(db_session, shop_id) -> list[Stock]:
    result = await db_session.execute(
        select(Stock).where(Stock.shop_id == shop_id)
    )
    return result.scalars().all()


class TestStocksAtomicity:
    async def test_adapter_error_keeps_previous_snapshot(
        self, db_session, ozon_shop, monkeypatch
    ):
        await _seed_stock(db_session, ozon_shop.id)
        adapter = FakeAdapter(orders=[], stocks_error=RuntimeError("api down"))
        _install_fake_adapter(monkeypatch, adapter)

        svc = SyncService(db_session)
        results = await svc.sync_shop(ozon_shop, credentials={})

        assert results["stocks"]["status"] == "error"
        stocks = await _shop_stocks(db_session, ozon_shop.id)
        assert len(stocks) == 1
        assert stocks[0].external_sku == "OLD-SKU"

    async def test_empty_adapter_result_keeps_previous_snapshot(
        self, db_session, ozon_shop, monkeypatch
    ):
        """WB-style no-op: adapter returns [] (feature disabled) — the old
        snapshot must survive instead of being wiped."""
        await _seed_stock(db_session, ozon_shop.id)
        adapter = FakeAdapter(orders=[], stocks=[])
        _install_fake_adapter(monkeypatch, adapter)

        svc = SyncService(db_session)
        results = await svc.sync_shop(ozon_shop, credentials={})

        assert results["stocks"]["status"] == "success"
        assert results["stocks"]["count"] == 0
        stocks = await _shop_stocks(db_session, ozon_shop.id)
        assert len(stocks) == 1
        assert stocks[0].external_sku == "OLD-SKU"

    async def test_successful_load_replaces_snapshot(
        self, db_session, ozon_shop, monkeypatch
    ):
        await _seed_stock(db_session, ozon_shop.id)
        adapter = FakeAdapter(
            orders=[],
            stocks=[
                {"external_sku": "NEW-1", "quantity": 3},
                {"external_sku": "NEW-2", "quantity": 7},
            ],
        )
        _install_fake_adapter(monkeypatch, adapter)

        svc = SyncService(db_session)
        results = await svc.sync_shop(ozon_shop, credentials={})

        assert results["stocks"]["status"] == "success", results["stocks"]
        assert results["stocks"]["count"] == 2
        stocks = await _shop_stocks(db_session, ozon_shop.id)
        assert {s.external_sku for s in stocks} == {"NEW-1", "NEW-2"}


class TestSyncLogFields:
    async def _run_successful_sync(self, db_session, ozon_shop, monkeypatch):
        adapter = FakeAdapter(
            orders=[
                make_order("SKU-L", 100, order_id="order-1"),
                make_order("SKU-L", 200, order_id="order-2"),
            ],
            stocks=[{"external_sku": "SKU-L", "quantity": 4}],
            adverts=[{"date": datetime.utcnow(), "external_sku": "SKU-L", "spend": Decimal("10")}],
            finance=[
                {"posting_number": None, "category": "commission", "amount": Decimal("-5")}
            ],
        )
        _install_fake_adapter(monkeypatch, adapter)
        svc = SyncService(db_session)
        date_from = datetime.utcnow() - timedelta(days=2)
        date_to = datetime.utcnow()
        results = await svc.sync_shop(
            ozon_shop,
            credentials={},
            date_from=date_from,
            date_to=date_to,
        )
        return results, date_from, date_to

    async def test_sync_log_populated(self, db_session, ozon_shop, monkeypatch):
        results, date_from, date_to = await self._run_successful_sync(
            db_session, ozon_shop, monkeypatch
        )
        assert results["status"] == "success"

        result = await db_session.execute(
            select(SyncLog).where(SyncLog.shop_id == ozon_shop.id)
        )
        log = result.scalars().one()
        assert log.status == "success"
        assert log.is_partial is False, log.sections
        assert log.date_from == date_from.date()
        assert log.date_to == date_to.date()
        assert log.rows_received == 2 + 1 + 1 + 1  # orders + stocks + adverts + finance
        assert log.rows_saved == log.rows_received
        assert log.started_at is not None
        assert log.created_at >= log.started_at
        assert set(log.sections) == {
            "orders",
            "stocks",
            "adverts",
            "prices",
            "finance",
            "balance",
        }

    async def test_sync_log_partial_on_section_error(
        self, db_session, ozon_shop, monkeypatch
    ):
        adapter = FakeAdapter(
            orders=[make_order("SKU-P", 100)],
            stocks=[],
            finance_error=RuntimeError("finance api down"),
        )
        _install_fake_adapter(monkeypatch, adapter)

        svc = SyncService(db_session)
        results = await svc.sync_shop(ozon_shop, credentials={})
        assert results["status"] == "success"  # shop-level status stays success
        assert results["finance"]["status"] == "error"

        result = await db_session.execute(
            select(SyncLog).where(SyncLog.shop_id == ozon_shop.id)
        )
        log = result.scalars().one()
        assert log.is_partial is True
        assert log.sections["finance"]["status"] == "error"
        assert log.rows_received == 1  # only the orders section
        assert log.rows_saved == 1


class TestInitialSyncAggregation:
    async def test_counts_orders_and_marks_partial(
        self, db_session, ozon_shop, monkeypatch
    ):
        """initial_sync must aggregate order counts from the "orders" section
        and report "partial" when some chunks loaded data and some failed."""

        class ChunkedAdapter(FakeAdapter):
            async def get_orders(self, date_from, date_to):
                # Early chunks succeed, later ones fail.
                if date_from < datetime.utcnow() - timedelta(days=200):
                    return [make_order("SKU-I", 100)]
                raise RuntimeError("chunk failed")

        async def _no_sleep(*args, **kwargs):
            return None

        # initial_sync backs off for 120s after a failed chunk; skip the wait.
        monkeypatch.setattr("asyncio.sleep", _no_sleep)
        _install_fake_adapter(monkeypatch, ChunkedAdapter(stocks=[]))
        svc = SyncService(db_session)

        overall = await svc.initial_sync(ozon_shop, days_back=365, credentials={})

        assert overall["chunks"] == 5  # 365 days / 90-day chunks (ozon)
        assert overall["sales"] == 2
        assert overall["status"] == "partial"
        assert overall["message"]
