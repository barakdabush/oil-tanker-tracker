"""Tests for StraitRiskFetcher (Straits.live + GDELT)."""

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.strait_risk_fetcher import StraitRiskFetcher, _parse_iso_date


def test_parse_iso_date_formats():
    assert _parse_iso_date("2026-11-18T12:00:00Z") == date(2026, 11, 18)
    assert _parse_iso_date("20261118T000000Z") == date(2026, 11, 18)
    assert _parse_iso_date("2026-11-17") == date(2026, 11, 17)
    assert _parse_iso_date(None) is None


@pytest.mark.asyncio
async def test_fetch_and_store_merges_straits_live_and_gdelt():
    def mock_get(url, params=None, **kwargs):
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        if "api/index" in url:
            resp.json.return_value = {
                "asOf": "2026-11-18T12:00:00Z",
                "indices": {
                    "crisisPressure": {
                        "value": 87,
                        "history": [{"iso": "2026-11-17T00:00:00Z", "value": 84}],
                    },
                    "escalationProbability": {
                        "value": 60,
                        "history": [{"iso": "2026-11-17T00:00:00Z", "value": 58}],
                    },
                },
            }
        elif "v1/stranded" in url:
            resp.json.return_value = {
                "points": [
                    {"iso": "2026-11-18T01:00:00Z", "count": 40},
                    {"iso": "2026-11-18T02:00:00Z", "count": 44},
                ]
            }
        elif "v1/events" in url:
            resp.json.return_value = {
                "events": [
                    {"occurredAt": "2026-11-18T03:00:00Z", "type": "strike"},
                    {"occurredAt": "2026-11-18T09:00:00Z", "type": "ship_incident"},
                ]
            }
        elif "gdeltproject.org" in url:
            mode = (params or {}).get("mode")
            val = 2.45 if mode == "timelinevol" else -4.12
            resp.json.return_value = {
                "timeline": [{"data": [{"date": "20261118T000000Z", "value": val}]}]
            }
        return resp

    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=mock_get)

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return mock_client
        async def __aexit__(self, exc_type, exc, tb):
            pass

    mock_session = MagicMock()
    mock_session.execute = AsyncMock()
    mock_session.commit = AsyncMock()

    class MockAsyncSession:
        async def __aenter__(self):
            return mock_session
        async def __aexit__(self, exc_type, exc, tb):
            pass

    with (
        patch("app.services.strait_risk_fetcher.httpx.AsyncClient", MockAsyncClient),
        patch("app.services.strait_risk_fetcher.async_session", return_value=MockAsyncSession()),
        patch("app.services.strait_risk_fetcher.asyncio.sleep", new_callable=AsyncMock),
    ):
        fetcher = StraitRiskFetcher()
        count = await fetcher.fetch_and_store()

    assert count == 2
    assert mock_session.execute.called
    assert mock_session.commit.called


@pytest.mark.asyncio
async def test_gdelt_retries_on_429():
    attempts = 0

    async def mock_get(url, params=None, **kwargs):
        nonlocal attempts
        attempts += 1
        resp = MagicMock()
        if attempts == 1:
            resp.status_code = 429
            resp.headers = {"Retry-After": "2"}
            return resp
        resp.status_code = 200
        resp.raise_for_status = MagicMock()
        resp.json.return_value = {
            "timeline": [{"data": [{"date": "20261118T000000Z", "value": 1.25}]}]
        }
        return resp

    mock_client = MagicMock()
    mock_client.get = AsyncMock(side_effect=mock_get)
    from collections import defaultdict
    by_date = defaultdict(dict)

    with patch("app.services.strait_risk_fetcher.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        fetcher = StraitRiskFetcher()
        await fetcher._fetch_gdelt_series(mock_client, "timelinevol", "gdelt_strait_risk_vol", by_date)

    assert attempts == 2
    mock_sleep.assert_awaited_once_with(2.0)
    assert by_date[date(2026, 11, 18)]["gdelt_strait_risk_vol"] == 1.25
