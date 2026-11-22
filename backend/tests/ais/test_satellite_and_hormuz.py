"""Unit tests for hormuz.now live ingestion, Copernicus Sentinel-1 SAR detector, and GFW SAR fetcher."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from app.services.gfw_sar_fetcher import parse_gfw_sar_response
from app.services.hormuz_now_ingestion import (
    SYNTHETIC_MMSI_BASE,
    normalize_vessel_name,
    parse_hormuz_snapshot,
    synthetic_mmsi_for_hormuz_id,
)
from app.services.sentinel1_sar_detector import (
    HORMUZ_BBOX,
    HORMUZ_WATER_MASK,
    detect_sar_targets,
)


def test_parse_hormuz_snapshot_filters_tankers_and_computes_timestamp():
    snap_ts = datetime(2026, 11, 22, 12, 0, 0, tzinfo=timezone.utc)
    snap_ms = int(snap_ts.timestamp() * 1000)

    payload = {
        "t": snap_ms,
        "n": 4,
        "fields": [
            "id", "name", "lat", "lon", "speed", "course", "heading",
            "category", "length", "flag", "type", "destination", "dwt",
            "ageMin", "commercial", "side",
        ],
        "rows": [
            ["6928383", "SHELIF", 25.7167, 55.086, 11.2, 290, 288, "tanker", 340, "GY", "Crude Oil Tanker", "SHARJAH OPL", 300361, 10, 1, 1],
            ["159094", "GUDRUN MAERSK", 24.5461, 58.2357, 0.5, 299, 6, "cargo", 366, "DK", "Container Ship", "AEKLF", 126591, 5, 1, 2],
            ["9935928", "PLC 021", 25.1756, 55.2039, 0, 104, 0, "pleasure", 20, "AE", "Pleasure Craft", "", 0, 6, 0, 1],
            ["777777", "OLD TANKER", 26.2, 56.4, 0, 0, 0, "tanker", 250, "PA", "Chemical Tanker", "FUJAIRAH", 50000, 500, 1, 2],
        ],
    }

    tankers = parse_hormuz_snapshot(payload)
    assert len(tankers) == 1
    t = tankers[0]
    assert t["ext_id"] == "6928383"
    assert t["name"] == "SHELIF"
    assert t["ship_type"] == 80
    assert t["dwt"] == 300361.0
    assert t["flag"] == "GY"
    # ageMin = 10 -> 11:50 UTC
    assert t["timestamp"] == datetime(2026, 11, 22, 11, 50, 0, tzinfo=timezone.utc)


def test_synthetic_mmsi_and_name_normalization():
    assert normalize_vessel_name("  M/T Gulf-Star 1 ") == "MTGULFSTAR1"
    m1 = synthetic_mmsi_for_hormuz_id("6928383")
    m2 = synthetic_mmsi_for_hormuz_id("6928383")
    assert m1 == m2
    assert SYNTHETIC_MMSI_BASE <= m1 < SYNTHETIC_MMSI_BASE + 10_000_000


@pytest.mark.asyncio
async def test_ingest_hormuz_now_snapshot_matches_real_mmsi_and_synthetic():
    from app.services import ais_ingestion

    ais_ingestion._vessel_name_to_mmsi.clear()
    ais_ingestion._known_tankers.clear()
    ais_ingestion._vessel_batch.clear()
    ais_ingestion._position_batch.clear()
    ais_ingestion._last_saved_positions.clear()
    ais_ingestion._last_seen_timestamps.clear()
    ais_ingestion._source_stats["hormuz_now"] = {
        "messages_received": 0,
        "tanker_positions": 0,
        "last_message_at": None,
    }

    # Register an existing real-MMSI tanker in the name index
    ais_ingestion._vessel_name_to_mmsi["SHELIF"] = 477123456
    ais_ingestion._known_tankers.add(477123456)

    ts = datetime(2026, 11, 22, 12, 0, 0, tzinfo=timezone.utc)
    tankers = [
        {
            "ext_id": "6928383",
            "name": "SHELIF",
            "lat": 26.25,
            "lon": 56.40,
            "speed": 10.5,
            "course": 270.0,
            "heading": 270.0,
            "ship_type": 80,
            "length": 340.0,
            "dwt": 300361.0,
            "flag": "GY",
            "destination": "FUJAIRAH",
            "timestamp": ts,
        },
        {
            "ext_id": "10760411",
            "name": "TSURUGIDAKE",
            "lat": 26.10,
            "lon": 56.55,
            "speed": 0.2,
            "course": 76.0,
            "heading": 143.0,
            "ship_type": 80,
            "length": 339.0,
            "dwt": 309000.0,
            "flag": "PA",
            "destination": "SOHAR",
            "timestamp": ts,
        },
    ]

    with patch.object(ais_ingestion.manager, "broadcast", new_callable=AsyncMock):
        await ais_ingestion._ingest_hormuz_now_snapshot(tankers)

    # SHELIF matched to real MMSI; TSURUGIDAKE got deterministic synthetic MMSI
    assert 477123456 in ais_ingestion._vessel_batch
    expected_syn = synthetic_mmsi_for_hormuz_id("10760411")
    assert expected_syn in ais_ingestion._vessel_batch
    assert ais_ingestion._source_stats["hormuz_now"]["tanker_positions"] == 2
    assert len(ais_ingestion._position_batch) == 2
    assert all(p["source"] == "hormuz_now" for p in ais_ingestion._position_batch)


def test_sentinel1_cfar_detects_tanker_in_hormuz_and_masks_island():
    h, w = 200, 300
    rng = np.random.default_rng(42)
    # Typical calm sea clutter: VH ~ -24 dB (linear ~0.004), VV ~ -16 dB (linear ~0.025)
    vh = np.clip(rng.normal(0.004, 0.0008, size=(h, w)), 1e-5, 0.02).astype(np.float32)
    vv = np.clip(rng.normal(0.025, 0.004, size=(h, w)), 1e-5, 0.1).astype(np.float32)
    mask = np.ones((h, w), dtype=np.float32)

    min_lon, min_lat, max_lon, max_lat = HORMUZ_BBOX

    def latlon_to_yx(lat: float, lon: float) -> tuple[int, int]:
        y = int((max_lat - lat) / (max_lat - min_lat) * h)
        x = int((lon - min_lon) / (max_lon - min_lon) * w)
        return y, x

    # Inject a bright oil tanker target in open water of Strait of Hormuz (26.35°N, 56.55°E)
    ty, tx = latlon_to_yx(26.35, 56.55)
    vh[ty - 1 : ty + 2, tx - 1 : tx + 2] = 0.25  # ~ -6 dB
    vv[ty - 1 : ty + 2, tx - 1 : tx + 2] = 0.80  # ~ -1 dB

    # Inject a bright target on Qeshm Island (26.78°N, 55.85°E) — should be masked out!
    iy, ix = latlon_to_yx(26.78, 55.85)
    vh[iy - 1 : iy + 2, ix - 1 : ix + 2] = 0.40
    vv[iy - 1 : iy + 2, ix - 1 : ix + 2] = 0.90

    detections = detect_sar_targets(vv, vh, mask, HORMUZ_BBOX, water_mask=HORMUZ_WATER_MASK)
    assert len(detections) == 1
    det = detections[0]
    assert abs(det["latitude"] - 26.35) < 0.03
    assert abs(det["longitude"] - 56.55) < 0.03
    assert det["radar_strength_db"] > -10.0
    assert det["estimated_length_m"] > 100.0


def test_parse_gfw_sar_response():
    payload = {
        "total": 1,
        "entries": [
            {
                "public-global-sar-presence:v3.0": [
                    {"date": "2026-11-15 02:00", "detections": 2, "lat": 26.31, "lon": 56.48},
                    {"date": "2026-11-15 02:00", "detections": 1, "lat": 25.92, "lon": 56.71},
                ]
            }
        ],
    }
    rows = parse_gfw_sar_response(payload, "Strait of Hormuz", is_dark=True)
    assert len(rows) == 2
    assert rows[0]["source"] == "gfw_sar"
    assert rows[0]["is_dark"] is True
    assert rows[0]["detection_count"] == 2
    assert rows[0]["chokepoint_name"] == "Strait of Hormuz"
    assert rows[0]["detected_at"] == datetime(2026, 11, 15, 2, 0, tzinfo=timezone.utc)
    assert rows[0]["dedup_key"] != rows[1]["dedup_key"]
