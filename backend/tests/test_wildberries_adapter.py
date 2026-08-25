"""Unit tests for WildberriesAdapter with faked HTTP responses.

Run:
    cd backend
    python -m pytest tests/test_wildberries_adapter.py -v

These tests do not hit the real WB API and therefore do not consume
rate limits. They validate parsing, pagination and edge-case handling.
"""

import pytest
from datetime import datetime
from decimal import Decimal
from typing import Any, List, Optional
from unittest.mock import AsyncMock, patch

from app.adapters.wildberries import WildberriesAdapter


class FakeResponse:
    """Synchronous fake httpx.Response for adapter tests."""

    def __init__(self, status_code: int = 200, json_data: Any = None, headers: Optional[dict] = None):
        self.status_code = status_code
        self._json = json_data
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


class FakeClient:
    """Async context manager returning FakeResponses in order."""

    def __init__(
        self,
        get_responses: Optional[List[FakeResponse]] = None,
        post_responses: Optional[List[FakeResponse]] = None,
    ):
        self._get_iter = iter(get_responses or [])
        self._post_iter = iter(post_responses or [])

    async def get(self, *args, **kwargs) -> FakeResponse:
        try:
            return next(self._get_iter)
        except StopIteration:
            return FakeResponse(200, {})

    async def post(self, *args, **kwargs) -> FakeResponse:
        try:
            return next(self._post_iter)
        except StopIteration:
            return FakeResponse(200, {})

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


@pytest.fixture
def adapter():
    return WildberriesAdapter(
        shop_id="test-shop-1",
        credentials={"api_key": "wb-secret-key"},
    )


@pytest.fixture(autouse=True)
def reset_throttle_and_skip_sleep():
    """Reset the global throttle and make all asyncio.sleep calls instant."""
    WildberriesAdapter._global_last_request_at = None
    with patch("asyncio.sleep", new_callable=AsyncMock):
        yield


def _patch_client(adapter, get_responses=None, post_responses=None):
    return patch.object(
        adapter,
        "_http_client",
        return_value=FakeClient(get_responses=get_responses, post_responses=post_responses),
    )


class TestAuthenticate:
    async def test_success(self, adapter):
        with _patch_client(adapter, get_responses=[FakeResponse(200, {"stocks": [{"sku": "SKU001", "amount": 10}]})]):
            result = await adapter.authenticate()

        assert result is True


class TestGetSales:
    async def test_success_and_storno(self, adapter):
        with _patch_client(
            adapter,
            get_responses=[
                FakeResponse(200, [
                    {
                        "date": "2026-08-20T10:00:00Z",
                        "lastChangeDate": "2026-08-20T10:00:00Z",
                        "supplierArticle": "SKU001",
                        "srid": "ej.abc123.0.0",
                        "finishedPrice": 1500.0,
                        "forPay": 1200.0,
                        "IsStorno": False,
                    },
                    {
                        "date": "2026-08-20T11:00:00Z",
                        "lastChangeDate": "2026-08-20T11:00:00Z",
                        "supplierArticle": "SKU002",
                        "srid": "ej.abc124.0.0",
                        "finishedPrice": 3000.0,
                        "forPay": 2500.0,
                        "IsStorno": True,
                    },
                ])
            ],
        ):
            sales = await adapter.get_sales(
                date_from=datetime(2026, 8, 20),
                date_to=datetime(2026, 8, 21),
            )

        assert len(sales) == 2
        assert sales[0]["external_sku"] == "SKU001"
        assert sales[0]["quantity"] == 1
        assert sales[0]["revenue"] == Decimal("1200.0")

        assert sales[1]["external_sku"] == "SKU002"
        assert sales[1]["quantity"] == -1
        assert sales[1]["is_return"] is True

    async def test_pagination_drops_boundary_row(self, adapter):
        boundary = "2026-08-20T12:00:00Z"
        # WB returns up to 80 000 rows per statistics request. If the first page
        # is smaller, the adapter stops paginating (it has reached the tail).
        # To test boundary-row deduplication we emulate a full first page.
        first_page = [
            {
                "date": "2026-08-20T10:00:00Z",
                "lastChangeDate": boundary,
                "supplierArticle": "SKU001",
                "srid": "ej.abc123.0.0",
                "finishedPrice": 1500.0,
                "forPay": 1200.0,
            }
            for _ in range(80000)
        ]
        with _patch_client(
            adapter,
            get_responses=[
                FakeResponse(200, first_page),
                FakeResponse(200, [
                    {
                        "date": "2026-08-20T12:00:00Z",
                        "lastChangeDate": boundary,
                        "supplierArticle": "SKU001",
                        "srid": "ej.abc123.0.0",
                        "finishedPrice": 1500.0,
                        "forPay": 1200.0,
                    },
                    {
                        "date": "2026-08-20T13:00:00Z",
                        "lastChangeDate": "2026-08-20T13:00:00Z",
                        "supplierArticle": "SKU002",
                        "srid": "ej.abc124.0.0",
                        "finishedPrice": 2000.0,
                        "forPay": 1800.0,
                    },
                ]),
            ],
        ):
            sales = await adapter.get_sales(
                date_from=datetime(2026, 8, 20),
                date_to=datetime(2026, 8, 21),
            )

        assert len(sales) == 80001
        assert sales[-1]["external_sku"] == "SKU002"


class TestGetOrders:
    async def test_success_and_cancel(self, adapter):
        with _patch_client(
            adapter,
            get_responses=[
                FakeResponse(200, [
                    {
                        "date": "2026-08-20T10:00:00Z",
                        "lastChangeDate": "2026-08-20T10:00:00Z",
                        "supplierArticle": "SKU001",
                        "srid": "ej.abc123.0.0",
                        "totalPrice": 1500.0,
                        "isCancel": False,
                    },
                    {
                        "date": "2026-08-20T11:00:00Z",
                        "lastChangeDate": "2026-08-20T11:00:00Z",
                        "supplierArticle": "SKU002",
                        "srid": "ej.abc124.0.0",
                        "totalPrice": 2000.0,
                        "isCancel": True,
                    },
                ])
            ],
        ):
            orders = await adapter.get_orders(
                date_from=datetime(2026, 8, 20),
                date_to=datetime(2026, 8, 21),
            )

        assert len(orders) == 2
        assert orders[0]["status"] == "ordered"
        assert orders[1]["status"] == "cancelled"


class TestStocksAndPrices:
    async def test_stocks_disabled(self, adapter):
        stocks = await adapter.get_stocks()
        assert stocks == []

    async def test_prices_disabled(self, adapter):
        prices = await adapter.get_prices()
        assert prices == []


class TestGetFinanceReport:
    async def test_success(self, adapter):
        with _patch_client(
            adapter,
            post_responses=[
                FakeResponse(200, [
                    {
                        "rrdId": 1,
                        "rrDate": "2026-08-20",
                        "srid": "ej.i417f68ba86af0fd75a9e865c8622d699.0.0",
                        "vendorCode": "SKU001",
                        "docTypeName": "Продажа",
                        "quantity": 2,
                        "retailAmount": 5000.0,
                        "retailPrice": 2500.0,
                        "ppvzSalesCommission": 500.0,
                        "deliveryService": 150.0,
                        "paidStorage": 20.0,
                        "returnAmount": 0.0,
                        "acquiringFee": 30.0,
                        "deduction": 10.0,
                        "penalty": 5.0,
                    }
                ])
            ],
        ):
            rows = await adapter.get_finance_report(
                date_from=datetime(2026, 8, 20),
                date_to=datetime(2026, 8, 21),
            )

        assert len(rows) == 1
        row = rows[0]
        assert row["external_sku"] == "SKU001"
        # srid should be normalized to the hex segment.
        assert row["external_id"] == "i417f68ba86af0fd75a9e865c8622d699"
        assert row["revenue"] == Decimal("5000.0")
        assert row["commission"] == Decimal("500.0")
        assert row["logistics"] == Decimal("150.0")
        assert row["storage"] == Decimal("20.0")
        assert row["acquiring"] == Decimal("30.0")
        assert row["other"] == Decimal("15.0")  # deduction + penalty

    async def test_return_sign(self, adapter):
        with _patch_client(
            adapter,
            post_responses=[
                FakeResponse(200, [
                    {
                        "rrdId": 2,
                        "rrDate": "2026-08-20",
                        "srid": "abc.0.0",
                        "vendorCode": "SKU001",
                        "docTypeName": "Возврат",
                        "quantity": 1,
                        "retailAmount": 1000.0,
                        "retailPrice": 1000.0,
                        "ppvzSalesCommission": 0.0,
                        "deliveryService": 0.0,
                        "paidStorage": 0.0,
                        "returnAmount": 0.0,
                        "acquiringFee": 0.0,
                        "deduction": 0.0,
                        "penalty": 0.0,
                    }
                ])
            ],
        ):
            rows = await adapter.get_finance_report(
                date_from=datetime(2026, 8, 20),
                date_to=datetime(2026, 8, 21),
            )

        assert rows[0]["quantity"] == -1
        assert rows[0]["revenue"] == Decimal("-1000.0")


class TestGetAdverts:
    async def test_fullstats_with_nm_breakdown(self, adapter):
        with _patch_client(
            adapter,
            get_responses=[
                # /adv/v1/promotion/count
                FakeResponse(200, {
                    "adverts": [
                        {
                            "status": 9,
                            "type": 1,
                            "advert_list": [{"advertId": 123}],
                        }
                    ]
                }),
                # /adv/v3/fullstats
                FakeResponse(200, [
                    {
                        "advertId": 123,
                        "days": [
                            {
                                "date": "2026-08-20T00:00:00",
                                "sum": 1000,
                                "views": 100,
                                "clicks": 10,
                                "ctr": 10.0,
                                "cpc": 100.0,
                                "orders": 2,
                                "cr": 2.0,
                                "apps": [
                                    {
                                        "nms": [
                                            {
                                                "nmId": 111,
                                                "sum": 600,
                                                "views": 60,
                                                "clicks": 6,
                                                "orders": 1,
                                            },
                                            {
                                                "nmId": 222,
                                                "sum": 400,
                                                "views": 40,
                                                "clicks": 4,
                                                "orders": 1,
                                            },
                                        ]
                                    }
                                ],
                            }
                        ],
                    }
                ]),
            ],
        ):
            adverts = await adapter.get_adverts(
                date_from=datetime(2026, 8, 20),
                date_to=datetime(2026, 8, 21),
            )

        assert len(adverts) == 2
        spend_by_sku = {a["external_sku"]: a["spend"] for a in adverts}
        assert spend_by_sku["111"] == Decimal("600")
        assert spend_by_sku["222"] == Decimal("400")


class TestGetBalance:
    async def test_success(self, adapter):
        with _patch_client(
            adapter,
            get_responses=[
                FakeResponse(200, {"current": 12345.67, "for_withdraw": 10000.0, "currency": "RUB"})
            ],
        ):
            balance = await adapter.get_balance()

        assert balance["balance"] == Decimal("12345.67")
        assert balance["available"] == Decimal("10000.0")
        assert balance["currency"] == "RUB"
        assert balance["payout_at"] is None
