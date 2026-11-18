"""Regression test for the AISGap.gap_hours bug fix."""

import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

from app.services.global_oil_feature_builder import GlobalOilFeatureBuilder


@pytest.mark.asyncio
async def test_aggregate_runs_without_attribute_error():
    """Test that aggregate() does not raise AttributeError for missing AISGap.gap_hours.
    
    This test mocks the database session to avoid needing a real database.
    Before the fix, this test would fail with AttributeError because the query
    references AISGap.gap_hours which does not exist.
    """
    # Create a mock session
    mock_session = MagicMock()
    
    # Configure execute to return a mock result
    def execute_side_effect(*args, **kwargs):
        result = MagicMock()
        result.scalar.return_value = 0
        result.one.return_value = SimpleNamespace(
            total_active=0,
            in_transit=0,
            at_port=0,
            avg_speed=None
        )
        return result
    
    mock_session.execute = AsyncMock(side_effect=execute_side_effect)
    mock_session.commit = AsyncMock()
    
    # Patch async_session to return an async context manager that yields mock_session
    class MockAsyncSession:
        async def __aenter__(self):
            return mock_session
        async def __aexit__(self, exc_type, exc, tb):
            pass
    
    with patch('app.services.global_oil_feature_builder.async_session', return_value=MockAsyncSession()):
        builder = GlobalOilFeatureBuilder()
        # This should not raise AttributeError
        await builder.aggregate(target_date=date(2026, 11, 17))
        
        # Verify that execute was called (i.e., the queries were attempted)
        assert mock_session.execute.called


@pytest.mark.asyncio
async def test_backfill_missing_days_aggregates_missing_and_yesterday():
    from datetime import datetime, timedelta, timezone

    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)
    two_days_ago = yesterday - timedelta(days=1)
    three_days_ago = yesterday - timedelta(days=2)

    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_scalars = MagicMock()
    # Pretend three_days_ago exists, but two_days_ago and yesterday are missing
    mock_scalars.all.return_value = [three_days_ago]
    mock_result.scalars.return_value = mock_scalars
    mock_session.execute = AsyncMock(return_value=mock_result)

    class MockAsyncSession:
        async def __aenter__(self):
            return mock_session
        async def __aexit__(self, exc_type, exc, tb):
            pass

    builder = GlobalOilFeatureBuilder()
    builder.aggregate = AsyncMock()

    with patch("app.services.global_oil_feature_builder.async_session", return_value=MockAsyncSession()):
        await builder.backfill_missing_days(days=3)

    called_dates = [call.kwargs["target_date"] for call in builder.aggregate.await_args_list]
    assert called_dates == [two_days_ago, yesterday, today]


@pytest.mark.asyncio
async def test_aggregate_computes_strait_traffic_and_delay_features():
    """Verify aggregate() computes per-strait transits, diversion share, delays, and PortWatch counts."""
    captured_values = {}

    def execute_side_effect(stmt, *args, **kwargs):
        result = MagicMock()
        sql_text = str(stmt)
        if "WITH daily_vessels" in sql_text:
            result.one.return_value = SimpleNamespace(
                total_active=10,
                in_transit=6,
                at_port=4,
                avg_speed=11.2,
            )
        elif "FROM chokepoint_transits ct" in sql_text:
            result.one.return_value = SimpleNamespace(
                total_transits=20,
                hormuz_transits=4,
                cape_transits=7,
                bab_transits=1,
                suez_transits=3,
                malacca_transits=2,
                panama_transits=1,
                turkish_transits=1,
                danish_transits=1,
                other_transits=15,
                avg_transit_min=145.5,
                cape_avg_transit_min=180.25,
                other_avg_transit_min=160.0,
                hormuz_bab_avg_transit_min=95.5,
            )
        elif "FROM portwatch_chokepoint_daily" in sql_text:
            result.one.return_value = SimpleNamespace(
                pw_cape_tankers=28,
                pw_other_tankers=112,
            )
        else:
            result.scalar.return_value = 0
            if hasattr(stmt, "table") and getattr(stmt.table, "name", "") == "oil_market_snapshots":
                # Capture the values passed to pg_insert(OilMarketSnapshot).values(**values)
                compiled = stmt.compile()
                captured_values.update(compiled.params)
        return result

    mock_session = MagicMock()
    mock_session.execute = AsyncMock(side_effect=execute_side_effect)
    mock_session.commit = AsyncMock()

    class MockAsyncSession:
        async def __aenter__(self):
            return mock_session
        async def __aexit__(self, exc_type, exc, tb):
            pass

    with patch("app.services.global_oil_feature_builder.async_session", return_value=MockAsyncSession()):
        builder = GlobalOilFeatureBuilder()
        await builder.aggregate(target_date=date(2026, 11, 18))

    assert captured_values["chokepoint_transits_24h"] == 20
    assert captured_values["strait_of_hormuz_transits"] == 4
    assert captured_values["cape_of_good_hope_transits"] == 7
    assert captured_values["bab_el_mandeb_transits"] == 1
    assert captured_values["suez_canal_transits"] == 3
    assert captured_values["strait_of_malacca_transits"] == 2
    assert captured_values["panama_canal_transits"] == 1
    assert captured_values["turkish_straits_transits"] == 1
    assert captured_values["danish_straits_transits"] == 1
    assert captured_values["other_straits_transits"] == 15
    assert captured_values["other_straits_traffic_share_pct"] == 75.0
    assert captured_values["avg_chokepoint_transit_min"] == 145.5
    assert captured_values["cape_of_good_hope_avg_transit_min"] == 180.25
    assert captured_values["other_straits_avg_transit_min"] == 160.0
    assert captured_values["hormuz_bab_avg_transit_min"] == 95.5
    assert captured_values["portwatch_cape_tankers"] == 28
    assert captured_values["portwatch_other_straits_tankers"] == 112