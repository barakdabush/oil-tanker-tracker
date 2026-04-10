"""Tests for market analytics API endpoints and feature aggregator."""

import pytest
from datetime import date, datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch, MagicMock

from fastapi.testclient import TestClient

from app.main import app
from app.schemas.global_oil_features import MarketSnapshotResponse
from app.services.global_oil_feature_builder import GlobalOilFeatureBuilder


class TestMarketSnapshotSchema:
    """Test Pydantic schema validation."""

    def test_schema_with_all_fields(self):
        data = MarketSnapshotResponse(
            snapshot_date=date(2024, 1, 1),
            total_active_vessels=150,
            vessels_in_transit=80,
            vessels_at_port=50,
            avg_fleet_speed=10.5,
            vessels_idle_gt_48h=5,
            dark_vessels_count=12,
            new_ais_gaps_24h=3,
            resolved_gaps_24h=2,
            avg_gap_duration_hours=8.5,
            sts_events_24h=1,
            sts_confirmed_24h=0,
            chokepoint_transits_24h=45,
            strait_of_hormuz_transits=20,
            cargo_events_24h=10,
            estimated_volume_barrels_24h=500000.0,
            brent_close_usd=82.50,
            wti_close_usd=78.25,
        )
        assert data.snapshot_date == date(2024, 1, 1)
        assert data.total_active_vessels == 150
        assert data.brent_close_usd == 82.50

    def test_schema_with_optional_nulls(self):
        data = MarketSnapshotResponse(snapshot_date=date(2024, 1, 1))
        assert data.snapshot_date == date(2024, 1, 1)
        assert data.total_active_vessels is None
        assert data.brent_close_usd is None
        assert data.wti_close_usd is None

    def test_schema_serialization(self):
        data = MarketSnapshotResponse(
            snapshot_date=date(2024, 6, 15),
            total_active_vessels=100,
            vessels_in_transit=60,
            brent_close_usd=85.0,
        )
        dumped = data.model_dump()
        assert dumped["snapshot_date"] == date(2024, 6, 15)
        assert dumped["total_active_vessels"] == 100
        assert dumped["vessels_in_transit"] == 60
        assert dumped["brent_close_usd"] == 85.0
        assert dumped["wti_close_usd"] is None


class TestMarketSnapshotsEndpoint:
    """Test the GET /api/market/snapshots endpoint."""

    def test_endpoint_registered(self):
        """Verify the global_oil_features router is registered on the app."""
        routes = [route.path for route in app.routes]
        assert "/api/global-oil-features/snapshots" in routes

    def test_response_model_fields(self):
        """Verify the response model has all expected fields."""
        fields = MarketSnapshotResponse.model_fields
        expected = [
            "snapshot_date",
            "total_active_vessels",
            "vessels_in_transit",
            "vessels_at_port",
            "avg_fleet_speed",
            "vessels_idle_gt_48h",
            "dark_vessels_count",
            "new_ais_gaps_24h",
            "resolved_gaps_24h",
            "avg_gap_duration_hours",
            "sts_events_24h",
            "sts_confirmed_24h",
            "chokepoint_transits_24h",
            "strait_of_hormuz_transits",
            "cargo_events_24h",
            "estimated_volume_barrels_24h",
            "brent_close_usd",
            "wti_close_usd",
        ]
        for field in expected:
            assert field in fields, f"Missing field: {field}"


class TestGlobalOilFeatureBuilder:
    """Test the GlobalOilFeatureBuilder service data shape logic."""

    def test_aggregator_instantiation(self):
        """Verify the builder can be instantiated."""
        builder = GlobalOilFeatureBuilder()
        assert builder is not None
        assert hasattr(builder, "aggregate")
        assert hasattr(builder, "run_periodic")
