"""Unit tests for OzonAdapter with mocked HTTP responses.

Run:
    cd backend
    python -m pytest tests/test_ozon_adapter.py -v
"""

import pytest
from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, Mock, patch

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
        mock_httpx_post.return_value.json = Mock(return_value={
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
        mock_httpx_post.return_value.json = Mock(return_value={"error": "Unauthorized"})

        result = await adapter.authenticate()

        assert result is False

    async def test_failure_404_old_endpoint(self, adapter, mock_httpx_post):
        """Simulate old v1 endpoint returning 404."""
        mock_httpx_post.return_value.status_code = 404
        mock_httpx_post.return_value.json = Mock(return_value={})

        result = await adapter.authenticate()

        assert result is False


class TestGetSales:
    async def test_success(self, adapter, mock_httpx_post):
        mock_httpx_post.return_value.status_code = 200
        mock_httpx_post.return_value.json = Mock(return_value={
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
        mock_httpx_post.return_value.json = Mock(return_value={"data": []})

        sales = await adapter.get_sales(
            date_from=datetime(2026, 8, 1),
            date_to=datetime(2026, 8, 13),
        )

        assert sales == []


class TestGetStocks:
    async def test_success(self, adapter, mock_httpx_post):
        mock_httpx_post.return_value.status_code = 200
        # v3/product/info/stocks shape: per-warehouse stock entries.
        mock_httpx_post.return_value.json = Mock(return_value={
            "items": [
                {
                    "offer_id": "SKU001",
                    "product_id": 1001,
                    "stocks": [
                        {
                            "warehouse_name": "Москва",
                            "present": 30,
                            "reserved": 2,
                        },
                        {
                            "warehouse_name": "Подольск",
                            "present": 20,
                            "reserved": 1,
                        },
                    ],
                },
            ]
        })

        stocks = await adapter.get_stocks()

        assert len(stocks) == 1
        assert stocks[0]["external_sku"] == "SKU001"
        assert stocks[0]["external_id"] == "1001"
        assert stocks[0]["warehouse"] == "Москва"
        assert stocks[0]["quantity"] == 50  # 30 + 20
        assert stocks[0]["in_way"] == 3  # 2 + 1


class TestGetPrices:
    async def test_success(self, adapter, mock_httpx_post):
        mock_httpx_post.return_value.status_code = 200
        mock_httpx_post.return_value.json = Mock(return_value={
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
        mock_httpx_post.return_value.json = Mock(side_effect=[
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
    async def test_skipped_without_performance_api(self, adapter, mock_httpx_post):
        """Ozon advert stats require the Performance API (OAuth), which the
        Seller API does not provide — the adapter must skip and return []."""
        adverts = await adapter.get_adverts(
            date_from=datetime(2026, 8, 1),
            date_to=datetime(2026, 8, 13),
        )

        assert adverts == []
        # No Seller API endpoint is called for adverts.
        mock_httpx_post.assert_not_called()
