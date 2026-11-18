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