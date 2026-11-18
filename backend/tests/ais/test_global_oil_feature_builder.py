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