"""Tests for P1 monthly analytics: ABC classification, reconciliation, plan-fact.

Run:
    cd backend
    python -m pytest tests/test_monthly_analytics.py -v
"""

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest

from app.models import (
    FinanceTransaction,
    Marketplace,
    Product,
    ProductShopMapping,
    Sale,
    Shop,
    Stock,
    User,
)

ABC_MONTH = "2026-07"
ABC_DAY = datetime(2026, 7, 15)
ABC_STOCK_DAY = datetime(2026, 7, 20)


def current_month_str() -> str:
    return date.today().strftime("%Y-%m")


def prev_month_str() -> str:
    first = date.today().replace(day=1)
    return (first - timedelta(days=1)).strftime("%Y-%m")


@pytest.fixture
async def wb_shop(db_session, test_user) -> Shop:
    shop = Shop(
        id=uuid.uuid4(),
        user_id=test_user.id,
        marketplace=Marketplace.wb,
        name="WB Monthly Shop",
        credentials={},
        is_active=True,
        sync_enabled=True,
        created_at=datetime.utcnow(),
    )
    db_session.add(shop)
    await db_session.commit()
    await db_session.refresh(shop)
    return shop


async def make_product(db_session, test_user, wb_shop, sku: str, cost) -> Product:
    product = Product(
        id=uuid.uuid4(),
        user_id=test_user.id,
        sku=sku,
        canonical_sku=sku,
        name=f"Product {sku}",
        cost_price=Decimal(str(cost)),
        min_price=Decimal("0"),
        created_at=datetime.utcnow(),
    )
    db_session.add(product)
    await db_session.flush()
    db_session.add(
        ProductShopMapping(
            id=uuid.uuid4(),
            product_id=product.id,
            shop_id=wb_shop.id,
            external_sku=sku,
        )
    )
    await db_session.commit()
    return product


async def make_sale(
    db_session,
    shop,
    sku: str,
    day: datetime,
    price,
    quantity: int = 1,
    commission=0,
    logistics=0,
    storage=0,
    advertising=0,
    returns=0,
    is_return: bool = False,
) -> Sale:
    price_dec = Decimal(str(price))
    sale = Sale(
        id=uuid.uuid4(),
        shop_id=shop.id,
        date=day,
        external_sku=sku,
        external_id=f"order-{sku}-{uuid.uuid4()}",
        quantity=quantity,
        price=price_dec,
        customer_price=price_dec,
        revenue=price_dec * quantity,
        commission=Decimal(str(commission)),
        logistics=Decimal(str(logistics)),
        storage=Decimal(str(storage)),
        advertising=Decimal(str(advertising)),
        returns=Decimal(str(returns)),
        is_return=is_return,
    )
    db_session.add(sale)
    return sale


async def make_finance(
    db_session,
    shop,
    day: datetime,
    category: str,
    amount,
    operation_type: str = "POSTING",
) -> FinanceTransaction:
    tx = FinanceTransaction(
        id=uuid.uuid4(),
        shop_id=shop.id,
        marketplace=shop.marketplace,
        operation_date=day,
        operation_type=operation_type,
        category=category,
        amount=Decimal(str(amount)),
        raw_data={"signed": True},
    )
    db_session.add(tx)
    return tx


async def make_stock(db_session, shop, sku: str, day: datetime, quantity: int) -> Stock:
    stock = Stock(
        id=uuid.uuid4(),
        shop_id=shop.id,
        date=day,
        external_sku=sku,
        warehouse="main",
        quantity=quantity,
    )
    db_session.add(stock)
    return stock


async def seed_abc_products(db_session, test_user, wb_shop):
    """A: маржа 43%; B: маржа 10%; C1: убыток; C2: оборот ниже порога; D: остаток без продаж."""
    await make_product(db_session, test_user, wb_shop, "SKU-A", 500)
    await make_product(db_session, test_user, wb_shop, "SKU-B", 100)
    await make_product(db_session, test_user, wb_shop, "SKU-C1", 0)
    await make_product(db_session, test_user, wb_shop, "SKU-C2", 0)
    await make_product(db_session, test_user, wb_shop, "SKU-D", 300)

    await make_sale(db_session, wb_shop, "SKU-A", ABC_DAY, 1000, 100, 5000, 2000)
    await make_sale(db_session, wb_shop, "SKU-B", ABC_DAY, 1000, 50, 40000)
    await make_sale(db_session, wb_shop, "SKU-C1", ABC_DAY, 1000, 10, 15000)
    await make_sale(db_session, wb_shop, "SKU-C2", ABC_DAY, 1000, 20, 1000)
    await make_stock(db_session, wb_shop, "SKU-D", ABC_STOCK_DAY, 50)
    await db_session.commit()


class TestAbcClassification:
    async def test_class_a(self, client, db_session, test_user, wb_shop, auth_headers):
        await seed_abc_products(db_session, test_user, wb_shop)
        resp = await client.get(
            f"/api/v1/reports/abc-classification?month={ABC_MONTH}",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        row = next(r for r in resp.json()["items"] if r["sku"] == "SKU-A")
        assert row["class"] == "A"
        assert Decimal(str(row["revenue"])) == Decimal("100000")
        assert Decimal(str(row["profit"])) == Decimal("43000")
        assert Decimal(str(row["margin_percent"])) == Decimal("43")

    async def test_class_b_low_margin(
        self, client, db_session, test_user, wb_shop, auth_headers
    ):
        await seed_abc_products(db_session, test_user, wb_shop)
        resp = await client.get(
            f"/api/v1/reports/abc-classification?month={ABC_MONTH}",
            headers=auth_headers,
        )
        row = next(r for r in resp.json()["items"] if r["sku"] == "SKU-B")
        assert row["class"] == "B"
        assert Decimal(str(row["margin_percent"])) == Decimal("10")

    async def test_class_c_negative_profit_and_low_turnover(
        self, client, db_session, test_user, wb_shop, auth_headers
    ):
        await seed_abc_products(db_session, test_user, wb_shop)
        resp = await client.get(
            f"/api/v1/reports/abc-classification?month={ABC_MONTH}",
            headers=auth_headers,
        )
        items = {r["sku"]: r for r in resp.json()["items"]}
        assert items["SKU-C1"]["class"] == "C"  # прибыль < 0
        assert items["SKU-C2"]["class"] == "C"  # оборот 20000 < порога 30000

    async def test_class_d_no_sales_with_stock(
        self, client, db_session, test_user, wb_shop, auth_headers
    ):
        await seed_abc_products(db_session, test_user, wb_shop)
        resp = await client.get(
            f"/api/v1/reports/abc-classification?month={ABC_MONTH}",
            headers=auth_headers,
        )
        row = next(r for r in resp.json()["items"] if r["sku"] == "SKU-D")
        assert row["class"] == "D"
        assert row["stock"] == 50
        assert row["items_sold"] == 0

    async def test_summary_and_profit_share(
        self, client, db_session, test_user, wb_shop, auth_headers
    ):
        await seed_abc_products(db_session, test_user, wb_shop)
        resp = await client.get(
            f"/api/v1/reports/abc-classification?month={ABC_MONTH}",
            headers=auth_headers,
        )
        data = resp.json()
        assert data["summary"] == {"A": 1, "B": 1, "C": 2, "D": 1}
        # Прибыль: A 43000, B 5000, C1 -5000, C2 19000 → доля A = 43/62.
        assert round(Decimal(str(data["a_profit_share_percent"])), 2) == Decimal(
            "69.35"
        )

    async def test_other_user_data_hidden(
        self, client, db_session, test_user, wb_shop, auth_headers
    ):
        await seed_abc_products(db_session, test_user, wb_shop)
        alien = User(
            id=uuid.uuid4(),
            email="alien-abc@example.com",
            password_hash="fake-hash",
            role="admin",
            created_at=datetime.utcnow(),
        )
        db_session.add(alien)
        await db_session.flush()
        alien_shop = Shop(
            id=uuid.uuid4(),
            user_id=alien.id,
            marketplace=Marketplace.wb,
            name="Alien Shop",
            credentials={},
        )
        db_session.add(alien_shop)
        await db_session.flush()
        await make_sale(db_session, alien_shop, "ALIEN-1", ABC_DAY, 5000, 10)
        await db_session.commit()

        resp = await client.get(
            f"/api/v1/reports/abc-classification?month={ABC_MONTH}",
            headers=auth_headers,
        )
        assert all(r["sku"] != "ALIEN-1" for r in resp.json()["items"])

    async def test_invalid_month_rejected(self, client, auth_headers):
        resp = await client.get(
            "/api/v1/reports/abc-classification?month=2026-13",
            headers=auth_headers,
        )
        assert resp.status_code == 422


class TestReconciliation:
    async def _seed(self, db_session, wb_shop, month: str, commission_tx=10000):
        day = datetime.strptime(f"{month}-15", "%Y-%m-%d")
        await make_sale(db_session, wb_shop, "SKU-R", day, 1000, 100, 10000, 5000)
        await make_sale(db_session, wb_shop, "SKU-R", day, 1000, 1, 0, 0, advertising=2000)
        await make_finance(db_session, wb_shop, day, "revenue", 100000)
        await make_finance(db_session, wb_shop, day, "commission", -commission_tx)
        await make_finance(db_session, wb_shop, day, "logistics", -5000)
        await make_finance(db_session, wb_shop, day, "advertising", -2000)
        await db_session.commit()

    def _row(self, data, key):
        return next(r for r in data["total"] if r["key"] == key)

    async def test_closed_when_matches(
        self, client, db_session, wb_shop, auth_headers
    ):
        month = prev_month_str()
        await self._seed(db_session, wb_shop, month)
        resp = await client.get(
            f"/api/v1/reports/reconciliation?month={month}", headers=auth_headers
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "closed"
        revenue = self._row(data, "revenue")
        assert Decimal(str(revenue["sales"])) == Decimal("101000")
        assert Decimal(str(revenue["accruals"])) == Decimal("100000")
        assert Decimal(str(revenue["discrepancy"])) == Decimal("1000")
        # 1000 / 101000 < 2% — статус closed сохраняется.
        profit = self._row(data, "profit")
        assert Decimal(str(profit["sales"])) == Decimal("84000")
        assert Decimal(str(profit["accruals"])) == Decimal("83000")
        assert data["payouts_available"] is False
        assert "оценочные" in data["note"]

    async def test_has_discrepancies(
        self, client, db_session, wb_shop, auth_headers
    ):
        month = prev_month_str()
        await self._seed(db_session, wb_shop, month, commission_tx=20000)
        resp = await client.get(
            f"/api/v1/reports/reconciliation?month={month}", headers=auth_headers
        )
        data = resp.json()
        assert data["status"] == "has_discrepancies"
        commission = self._row(data, "commission")
        assert Decimal(str(commission["discrepancy"])) == Decimal("-10000")

    async def test_preliminary_for_current_month(
        self, client, db_session, wb_shop, auth_headers
    ):
        month = current_month_str()
        await self._seed(db_session, wb_shop, month, commission_tx=20000)
        resp = await client.get(
            f"/api/v1/reports/reconciliation?month={month}", headers=auth_headers
        )
        assert resp.json()["status"] == "preliminary"

    async def test_payouts_detected(
        self, client, db_session, wb_shop, auth_headers
    ):
        month = prev_month_str()
        await self._seed(db_session, wb_shop, month)
        day = datetime.strptime(f"{month}-20", "%Y-%m-%d")
        await make_finance(
            db_session, wb_shop, day, "other", 50000, operation_type="payout"
        )
        await db_session.commit()
        resp = await client.get(
            f"/api/v1/reports/reconciliation?month={month}", headers=auth_headers
        )
        data = resp.json()
        assert data["payouts_available"] is True
        profit = self._row(data, "profit")
        assert Decimal(str(profit["payouts"])) == Decimal("50000")
        # Выплата не должна попасть в начисления (category "other" пропущена).
        assert Decimal(str(self._row(data, "commission")["accruals"])) == Decimal(
            "10000"
        )

    async def test_split_by_marketplace(
        self, client, db_session, test_user, wb_shop, auth_headers
    ):
        month = prev_month_str()
        await self._seed(db_session, wb_shop, month)
        resp = await client.get(
            f"/api/v1/reports/reconciliation?month={month}", headers=auth_headers
        )
        data = resp.json()
        assert [block["key"] for block in data["by_marketplace"]] == ["wb"]
        wb_revenue = next(
            r for r in data["by_marketplace"][0]["rows"] if r["key"] == "revenue"
        )
        assert Decimal(str(wb_revenue["sales"])) == Decimal("101000")


class TestMonthlyTargets:
    async def test_crud(self, client, auth_headers):
        month = "2026-08"
        payload = {"targets": {"revenue": 100000, "profit": 40000, "margin": 43}}
        resp = await client.put(
            f"/api/v1/reports/monthly-targets/{month}",
            json=payload,
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["month"] == "2026-08-01"
        assert resp.json()["targets"]["revenue"] == 100000

        resp = await client.get(
            f"/api/v1/reports/monthly-targets/{month}", headers=auth_headers
        )
        assert resp.status_code == 200
        assert resp.json()["targets"]["profit"] == 40000

        resp = await client.put(
            f"/api/v1/reports/monthly-targets/{month}",
            json={"targets": {"revenue": 120000}},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["targets"] == {"revenue": 120000}

    async def test_404_when_missing(self, client, auth_headers):
        resp = await client.get(
            "/api/v1/reports/monthly-targets/2026-01", headers=auth_headers
        )
        assert resp.status_code == 404

    async def test_unknown_metric_rejected(self, client, auth_headers):
        resp = await client.put(
            "/api/v1/reports/monthly-targets/2026-08",
            json={"targets": {"unicorns": 5}},
            headers=auth_headers,
        )
        assert resp.status_code == 422

    async def test_other_user_target_hidden(
        self, client, db_session, test_user, auth_headers
    ):
        from app.models import MonthlyTarget

        alien = User(
            id=uuid.uuid4(),
            email="alien-target@example.com",
            password_hash="fake-hash",
            role="admin",
            created_at=datetime.utcnow(),
        )
        db_session.add(alien)
        await db_session.flush()
        db_session.add(
            MonthlyTarget(
                id=uuid.uuid4(),
                user_id=alien.id,
                month=date(2026, 8, 1),
                targets={"revenue": 1},
            )
        )
        await db_session.commit()

        resp = await client.get(
            "/api/v1/reports/monthly-targets/2026-08", headers=auth_headers
        )
        assert resp.status_code == 404


class TestPlanFact:
    async def test_plan_fact_statuses(
        self, client, db_session, test_user, wb_shop, auth_headers
    ):
        month = prev_month_str()
        day = datetime.strptime(f"{month}-10", "%Y-%m-%d")
        await make_product(db_session, test_user, wb_shop, "SKU-P", 500)
        # revenue 100000, расходы 7000, себестоимость 500*100 → profit 43000, маржа 43%.
        await make_sale(db_session, wb_shop, "SKU-P", day, 1000, 100, 5000, 2000)
        await db_session.commit()

        # Цель = факту → on_track.
        await client.put(
            f"/api/v1/reports/monthly-targets/{month}",
            json={
                "targets": {
                    "revenue": 100000,
                    "profit": 43000,
                    "margin": 43,
                    "orders": 1,
                    "returns": 0,
                    "stock": 500,
                }
            },
            headers=auth_headers,
        )
        resp = await client.get(
            f"/api/v1/reports/plan-fact?month={month}", headers=auth_headers
        )
        assert resp.status_code == 200
        rows = {r["metric"]: r for r in resp.json()["rows"]}
        assert rows["revenue"]["status"] == "on_track"
        assert rows["revenue"]["deviation"] == 0
        assert rows["profit"]["status"] == "on_track"
        assert rows["orders"]["fact"] == 1
        assert rows["stock"]["fact"] is None
        assert "note" in rows["stock"]

        # План 80000 при факте 100000 → +25% → off_track.
        await client.put(
            f"/api/v1/reports/monthly-targets/{month}",
            json={"targets": {"revenue": 80000}},
            headers=auth_headers,
        )
        resp = await client.get(
            f"/api/v1/reports/plan-fact?month={month}", headers=auth_headers
        )
        row = next(r for r in resp.json()["rows"] if r["metric"] == "revenue")
        assert row["status"] == "off_track"
        assert Decimal(str(row["deviation_percent"])) == Decimal("25")

        # План 93000 → +7.5% → at_risk.
        await client.put(
            f"/api/v1/reports/monthly-targets/{month}",
            json={"targets": {"revenue": 93000}},
            headers=auth_headers,
        )
        resp = await client.get(
            f"/api/v1/reports/plan-fact?month={month}", headers=auth_headers
        )
        row = next(r for r in resp.json()["rows"] if r["metric"] == "revenue")
        assert row["status"] == "at_risk"
        assert Decimal(str(row["deviation"])) == Decimal("7000")

    async def test_plan_fact_404_without_targets(self, client, auth_headers):
        resp = await client.get(
            "/api/v1/reports/plan-fact?month=2026-01", headers=auth_headers
        )
        assert resp.status_code == 404
