"""Unit tests for OzonAdapter with mocked HTTP responses.

Run:
    cd backend
    python -m pytest tests/test_ozon_adapter.py -v
"""

import pytest
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from app.adapters.ozon import OzonAdapter


@pytest.fixture
def adapter():
    return OzonAdapter(
        shop_id="test-shop-1",
        credentials={"client_id": "12345", "api_key": "secret-key"},
    )


@pytest.fixture
def mock_httpx_post():
    """Patch httpx.AsyncClient.post for all tests."""
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock:
        yield mock


class TestAuthenticate:
    async def test_success(self, adapter, mock_httpx_post):
        mock_httpx_post.return_value.status_code = 200
        mock_httpx_post.return_value.json = AsyncMock(return_value={
            "warehouses": [
                {"warehouse_id": 1, "name": "Москва"},
                {"warehouse_id": 2, "name": "Подольск"},
            ]
        })

        result = await adapter.authenticate()

        assert result is True
        # Verify v2 endpoint was called
        args, kwargs = mock_httpx_post.call_args
        assert "v2/warehouse/list" in args[0]
        assert kwargs["headers"]["Client-Id"] == "12345"
        assert kwargs["headers"]["Api-Key"] == "secret-key"

    async def test_failure_401(self, adapter, mock_httpx_post):
        mock_httpx_post.return_value.status_code = 401
        mock_httpx_post.return_value.json = AsyncMock(return_value={"error": "Unauthorized"})

        result = await adapter.authenticate()

        assert result is False

    async def test_failure_404_old_endpoint(self, adapter, mock_httpx_post):
        """Simulate old v1 endpoint returning 404."""
        mock_httpx_post.return_value.status_code = 404
        mock_httpx_post.return_value.json = AsyncMock(return_value={})

        result = await adapter.authenticate()

        assert result is False


class TestGetSales:
    async def test_success(self, adapter, mock_httpx_post):
        mock_httpx_post.return_value.status_code = 200
        mock_httpx_post.return_value.json = AsyncMock(return_value={
            "data": [
                {
                    "dimensions": [{"sku": "SKU001", "day": "2026-08-10"}],
                    "metrics": [5, 15000.0, 1],
                },
                {
                    "dimensions": [{"sku": "SKU002", "day": "2026-08-10"}],
                    "metrics": [3, 9000.0, 0],
                },
            ]
        })

        sales = await adapter.get_sales(
            date_from=datetime(2026, 8, 1),
            date_to=datetime(2026, 8, 13),
        )

        assert len(sales) == 2
        assert sales[0]["external_sku"] == "SKU001"
        assert sales[0]["quantity"] == 5
        assert sales[0]["revenue"] == Decimal("15000.0")
        assert sales[1]["quantity"] == 3

    async def test_empty_response(self, adapter, mock_httpx_post):
        mock_httpx_post.return_value.status_code = 200
        mock_httpx_post.return_value.json = AsyncMock(return_value={"data": []})

        sales = await adapter.get_sales(
            date_from=datetime(2026, 8, 1),
            date_to=datetime(2026, 8, 13),
        )

        assert sales == []


class TestGetStocks:
    async def test_success(self, adapter, mock_httpx_post):
        mock_httpx_post.return_value.status_code = 200
        mock_httpx_post.return_value.json = AsyncMock(return_value={
            "items": [
                {
                    "offer_id": "SKU001",
                    "sku": 1001,
                    "warehouse_name": "Москва",
                    "quantity": 50,
                    "in_way_to_client": 2,
                    "in_way_from_client": 1,
                },
            ]
        })

        stocks = await adapter.get_stocks()

        assert len(stocks) == 1
        assert stocks[0]["external_sku"] == "SKU001"
        assert stocks[0]["quantity"] == 50
        assert stocks[0]["in_way"] == 3  # 2 + 1


class TestGetPrices:
    async def test_success(self, adapter, mock_httpx_post):
        mock_httpx_post.return_value.status_code = 200
        mock_httpx_post.return_value.json = AsyncMock(return_value={
            "items": [
                {"offer_id": "SKU001", "sku": 1001, "price": 2999.0, "discount": 10},
                {"offer_id": "SKU002", "sku": 1002, "price": 4990.0, "discount": 0},
            ]
        })

        prices = await adapter.get_prices()

        assert len(prices) == 2
        assert prices[0]["price"] == Decimal("2999.0")
        assert prices[0]["discount"] == 10


class TestGetOrders:
    async def test_fbo_and_fbs(self, adapter, mock_httpx_post):
        mock_httpx_post.return_value.status_code = 200
        # First call = FBO, second = FBS
        mock_httpx_post.return_value.json = AsyncMock(side_effect=[
            {
                "result": [
                    {
                        "posting_number": "FBO-001",
                        "created_at": "2026-08-10T12:00:00Z",
                        "status": "delivered",
                        "products": [
                            {"offer_id": "SKU001", "sku": 1001, "quantity": 2, "price": "1500.00"},
                        ],
                    }
                ]
            },
            {
                "result": {
                    "postings": [
                        {
                            "posting_number": "FBS-001",
                            "created_at": "2026-08-11T10:00:00Z",
                            "status": "awaiting_packaging",
                            "products": [
                                {"offer_id": "SKU002", "sku": 1002, "quantity": 1, "price": "3000.00"},
                            ],
                        }
                    ]
                }
            },
        ])

        orders = await adapter.get_orders(
            date_from=datetime(2026, 8, 1),
            date_to=datetime(2026, 8, 13),
        )

        assert len(orders) == 2
        assert orders[0]["external_sku"] == "SKU001"
        assert orders[0]["quantity"] == 2
        assert orders[1]["external_sku"] == "SKU002"
        assert orders[1]["status"] == "awaiting_packaging"


class TestGetAdverts:
    async def test_success(self, adapter, mock_httpx_post):
        mock_httpx_post.return_value.status_code = 200
        mock_httpx_post.return_value.json = AsyncMock(side_effect=[
            {"campaigns": [{"campaignId": 101, "title": "Test Campaign"}]},
            {
                "products": [
                    {
                        "offerId": "SKU001",
                        "views": 1000,
                        "clicks": 50,
                        "ctr": 5.0,
                        "cpc": 15.0,
                        "moneySpent": 750.0,
                        "orders": 3,
                        "cr": 6.0,
                    }
                ]
            },
        ])

        adverts = await adapter.get_adverts(
            date_from=datetime(2026, 8, 1),
            date_to=datetime(2026, 8, 13),
        )

        assert len(adverts) == 1
        assert adverts[0]["campaign_id"] == "101"
        assert adverts[0]["spend"] == Decimal("750.0")
        assert adverts[0]["orders"] == 3
