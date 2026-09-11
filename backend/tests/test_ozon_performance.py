"""Unit tests for Ozon Performance API advert stats with mocked HTTP.

Run:
    cd backend
    python -m pytest tests/test_ozon_performance.py -v
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
        credentials={
            "client_id": "12345",
            "api_key": "secret-key",
            "performance_client_id": "perf-client-1",
            "performance_access_token": "perf-secret-token",
        },
    )


@pytest.fixture
def mock_httpx_post():
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock:
        yield mock


@pytest.fixture
def mock_httpx_get():
    with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock:
        yield mock


CAMPAIGNS_RESPONSE = {
    "items": [
        {"id": 111, "title": "Campaign A", "state": "CAMPAIGN_STATE_ACTIVE"},
        {"id": 222, "title": "Campaign B", "state": "CAMPAIGN_STATE_ACTIVE"},
    ]
}

REPORT_OK = {
    "rows": [
        {
            "date": "2026-08-10",
            "campaignId": 111,
            "sku": "SKU001",
            "views": 1000,
            "clicks": 50,
            "spend": 500.0,
            "orders": 5,
        },
        {
            "date": "2026-08-10",
            "campaignId": 222,
            "sku": "",
            "views": 400,
            "clicks": 20,
            "spend": 300.0,
            "orders": 0,
        },
    ]
}


class TestGetAdvertsPerformance:
    async def test_success_create_poll_report(
        self, adapter, mock_httpx_post, mock_httpx_get
    ):
        mock_httpx_post.return_value.status_code = 200
        mock_httpx_post.return_value.raise_for_status = Mock()
        mock_httpx_post.return_value.json = Mock(side_effect=[
            CAMPAIGNS_RESPONSE,            # list campaigns
            {"UUID": "report-uuid-1"},     # create daily-report
        ])
        mock_httpx_get.return_value.status_code = 200
        mock_httpx_get.return_value.raise_for_status = Mock()
        mock_httpx_get.return_value.json = Mock(side_effect=[
            {"state": "PENDING"},                       # first poll
            {"state": "PROCESSING"},                    # second poll
            {"state": "OK", "report": REPORT_OK},       # third poll
        ])

        with patch("app.adapters.ozon.asyncio.sleep", new=AsyncMock()):
            adverts = await adapter.get_adverts(
                date_from=datetime(2026, 8, 1),
                date_to=datetime(2026, 8, 13),
            )

        assert len(adverts) == 2
        row = adverts[0]
        assert row["date"] == datetime(2026, 8, 10)
        assert row["campaign_id"] == "111"
        assert row["external_sku"] == "SKU001"
        assert row["views"] == 1000
        assert row["clicks"] == 50
        assert row["spend"] == Decimal("500.0")
        assert row["orders"] == 5
        assert row["ctr"] == Decimal("5.0")      # 50/1000*100
        assert row["cpc"] == Decimal("10.0")     # 500/50
        assert row["cr"] == Decimal("10.0")      # 5/50*100

        # Second row: campaign-level (no SKU), zero orders -> zero cpc/cr.
        row2 = adverts[1]
        assert row2["external_sku"] == ""
        assert row2["ctr"] == Decimal("5.0")
        assert row2["cpc"] == Decimal("15.0")    # 300/20
        assert row2["cr"] == Decimal("0")

        # Campaign list and report create hit the Performance host.
        post_urls = [c.args[0] for c in mock_httpx_post.call_args_list]
        assert post_urls[0] == "/api/client/campaign"
        assert post_urls[1] == "/api/client/statistics/daily-report"
        # Poll endpoint hit 3 times until OK.
        assert mock_httpx_get.call_count == 3
        get_kwargs = mock_httpx_get.call_args.kwargs
        assert get_kwargs["params"]["UUID"] == "report-uuid-1"

    async def test_no_performance_keys_returns_empty(self, adapter):
        adapter.performance_client_id = ""
        adapter.performance_access_token = ""

        adverts = await adapter.get_adverts(
            date_from=datetime(2026, 8, 1),
            date_to=datetime(2026, 8, 13),
        )

        assert adverts == []

    async def test_report_timeout_returns_empty(
        self, adapter, mock_httpx_post, mock_httpx_get
    ):
        mock_httpx_post.return_value.status_code = 200
        mock_httpx_post.return_value.raise_for_status = Mock()
        mock_httpx_post.return_value.json = Mock(side_effect=[
            CAMPAIGNS_RESPONSE,
            {"UUID": "report-uuid-slow"},
        ])
        mock_httpx_get.return_value.status_code = 200
        mock_httpx_get.return_value.raise_for_status = Mock()
        mock_httpx_get.return_value.json = Mock(
            return_value={"state": "PROCESSING"}
        )

        with patch("app.adapters.ozon.asyncio.sleep", new=AsyncMock()):
            adverts = await adapter.get_adverts(
                date_from=datetime(2026, 8, 1),
                date_to=datetime(2026, 8, 13),
            )

        assert adverts == []
        assert mock_httpx_get.call_count == 10

    async def test_api_error_returns_empty(
        self, adapter, mock_httpx_post, mock_httpx_get
    ):
        import httpx

        response = httpx.Response(401, request=httpx.Request("POST", "http://x"))
        mock_httpx_post.side_effect = httpx.HTTPStatusError(
            "401", request=response.request, response=response
        )

        adverts = await adapter.get_adverts(
            date_from=datetime(2026, 8, 1),
            date_to=datetime(2026, 8, 13),
        )

        assert adverts == []
        mock_httpx_get.assert_not_called()
