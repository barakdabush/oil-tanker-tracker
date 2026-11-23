"""Unit tests for AIS position validation, teleport/spoof filtering, trail despiking, and backward DB cleanup."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import ais_ingestion
from app.services.position_cleanup import cleanup_corrupted_positions
from app.utils.geo import despike_positions, is_plausible_move, is_valid_coordinate


@pytest.fixture(autouse=True)
def reset_ingestion_state(monkeypatch):
    ais_ingestion._known_tankers.clear()
    ais_ingestion._known_non_tankers.clear()
    ais_ingestion._pending_positions.clear()
    ais_ingestion._vessel_batch.clear()
    ais_ingestion._position_batch.clear()
    ais_ingestion._last_saved_positions.clear()
    ais_ingestion._last_trusted_positions.clear()
    ais_ingestion._suspect_positions.clear()
    ais_ingestion._last_vessel_db_updates.clear()
    ais_ingestion._last_ui_updates.clear()
    ais_ingestion._last_seen_timestamps.clear()
    ais_ingestion._known_drafts.clear()
    ais_ingestion._source_stats["aisstream"]["dropped_glitches"] = 0
    ais_ingestion._source_stats["openwaters"]["dropped_glitches"] = 0

    broadcasts = []

    async def fake_broadcast(payload):
        broadcasts.append(payload)

    monkeypatch.setattr(ais_ingestion.manager, "broadcast", fake_broadcast)
    return broadcasts


def _pos_msg(mmsi: int, lat, lon, time_utc: str, source: str = "aisstream", valid=True):
    payload = {"UserID": mmsi, "Sog": 2.0, "Valid": valid}
    if lat is not None:
        payload["Latitude"] = lat
    if lon is not None:
        payload["Longitude"] = lon
    return {
        "MessageType": "PositionReport",
        "MetaData": {
            "MMSI": mmsi,
            "ShipName": "INTAN GLORY",
            "time_utc": time_utc,
            "Source": source,
        },
        "Message": {"PositionReport": payload},
    }


def test_is_valid_coordinate():
    assert is_valid_coordinate(26.0332, 55.9168) is True
    assert is_valid_coordinate(0.0, 0.0) is False
    assert is_valid_coordinate(0.0002, -0.0001) is False
    assert is_valid_coordinate(91.0, 55.0) is False
    assert is_valid_coordinate(26.0, 181.0) is False
    assert is_valid_coordinate(None, 55.0) is False
    assert is_valid_coordinate(float("nan"), 55.0) is False
    assert is_valid_coordinate(float("inf"), 55.0) is False


def test_parse_ais_timestamp_handles_iso_and_go_format():
    fallback = datetime(2026, 11, 22, 20, 0, 0, tzinfo=timezone.utc)
    dt_iso = ais_ingestion._parse_ais_timestamp("2026-11-22T18:26:22Z", fallback)
    assert dt_iso == datetime(2026, 11, 22, 18, 26, 22, tzinfo=timezone.utc)

    dt_go = ais_ingestion._parse_ais_timestamp("2026-11-22 18:26:22.749439468 +0000 UTC", fallback)
    assert dt_go == datetime(2026, 11, 22, 18, 26, 22, 749439, tzinfo=timezone.utc)


def test_is_plausible_move():
    t0 = datetime(2026, 11, 22, 18, 0, 0, tzinfo=timezone.utc)
    # Normal tanker speed (~15 knots -> ~28 km in 1 hour)
    assert is_plausible_move(26.0, 56.0, t0, 26.25, 56.0, t0 + timedelta(hours=1)) is True
    # Small GPS jitter (< 5 km) even with 0s delta
    assert is_plausible_move(26.0, 56.0, t0, 26.01, 56.01, t0) is True
    # Teleport from Hormuz to Antarctica in 5 minutes -> impossible
    assert is_plausible_move(26.03, 55.92, t0, -75.0, -170.0, t0 + timedelta(minutes=5)) is False
    # Legitimate 5-day voyage (1000 km in 120 hours = ~4.5 knots) -> plausible
    assert is_plausible_move(26.0, 56.0, t0, 20.0, 64.0, t0 + timedelta(hours=120)) is True


@pytest.mark.asyncio
async def test_invalid_and_null_island_packets_dropped(reset_ingestion_state):
    mmsi = 352002032
    ais_ingestion._known_tankers.add(mmsi)

    await ais_ingestion._process_message(_pos_msg(mmsi, 0.0, 0.0, "2026-11-22T18:00:00Z"))
    await ais_ingestion._process_message(_pos_msg(mmsi, None, None, "2026-11-22T18:01:00Z"))
    await ais_ingestion._process_message(_pos_msg(mmsi, 26.03, 55.92, "2026-11-22T18:02:00Z", valid=False))

    assert len(ais_ingestion._position_batch) == 0
    assert len(ais_ingestion._vessel_batch) == 0
    assert len(reset_ingestion_state) == 0
    assert ais_ingestion._source_stats["aisstream"]["dropped_glitches"] == 3


@pytest.mark.asyncio
async def test_hormuz_teleport_spike_does_not_bypass_throttle_or_pollute_db(reset_ingestion_state):
    """Reproduces the 352002032 bug: anchored in Hormuz, 1 corrupted jump must be dropped."""
    mmsi = 352002032
    ais_ingestion._known_tankers.add(mmsi)

    # 1. Valid position in Strait of Hormuz
    await ais_ingestion._process_message(_pos_msg(mmsi, 26.0332, 55.9168, "2026-11-22T18:00:00Z"))
    assert len(ais_ingestion._position_batch) == 1
    assert len(reset_ingestion_state) == 1

    # 2. Corrupted jump to "end of the world" 5 minutes later -> must be dropped, NOT saved
    await ais_ingestion._process_message(_pos_msg(mmsi, -82.5, -175.0, "2026-11-22T18:05:00Z"))
    assert len(ais_ingestion._position_batch) == 1
    assert ais_ingestion._vessel_batch[mmsi]["last_lat"] == pytest.approx(26.0332)
    assert ais_ingestion._vessel_batch[mmsi]["last_lon"] == pytest.approx(55.9168)
    assert len(reset_ingestion_state) == 1
    assert ais_ingestion._source_stats["aisstream"]["dropped_glitches"] == 1

    # 3. Next valid Hormuz position 1 minute later (still within 30-min throttle) -> accepted cleanly
    await ais_ingestion._process_message(_pos_msg(mmsi, 26.0335, 55.9170, "2026-11-22T18:06:00Z"))
    assert len(reset_ingestion_state) == 2
    assert reset_ingestion_state[-1]["lat"] == pytest.approx(26.0335)
    assert mmsi not in ais_ingestion._suspect_positions


@pytest.mark.asyncio
async def test_quorum_recovers_from_previously_corrupted_db_anchor(reset_ingestion_state):
    """If DB seeded a corrupted last position, 3 consistent Hormuz reports recover automatically."""
    mmsi = 352002032
    ais_ingestion._known_tankers.add(mmsi)
    ais_ingestion._last_trusted_positions[mmsi] = {
        "lat": -82.5,
        "lon": -175.0,
        "time": datetime(2026, 11, 22, 18, 0, 0, tzinfo=timezone.utc),
    }

    # First 2 reports at Hormuz are buffered as suspects
    await ais_ingestion._process_message(_pos_msg(mmsi, 26.0330, 55.9165, "2026-11-22T18:01:00Z"))
    await ais_ingestion._process_message(_pos_msg(mmsi, 26.0331, 55.9166, "2026-11-22T18:02:00Z"))
    assert len(reset_ingestion_state) == 0

    # 3rd consistent report reaches quorum -> accepted and becomes new trusted anchor
    await ais_ingestion._process_message(_pos_msg(mmsi, 26.0332, 55.9168, "2026-11-22T18:03:00Z"))
    assert len(reset_ingestion_state) == 1
    assert reset_ingestion_state[0]["lat"] == pytest.approx(26.0332)
    assert ais_ingestion._last_trusted_positions[mmsi]["lat"] == pytest.approx(26.0332)
    assert mmsi not in ais_ingestion._suspect_positions


def test_despike_positions_handles_start_middle_end_and_null_island():
    t0 = datetime(2026, 11, 22, 12, 0, 0, tzinfo=timezone.utc)

    def pt(minutes: int, lat: float, lon: float):
        return SimpleNamespace(time=t0 + timedelta(minutes=minutes), latitude=lat, longitude=lon)

    track = [
        pt(0, -80.0, -170.0),   # corrupted first point
        pt(5, 26.03, 55.91),    # Hormuz 1
        pt(10, 26.031, 55.912), # Hormuz 2 (replaces corrupted first point)
        pt(15, 0.0, 0.0),       # Null Island
        pt(20, 26.032, 55.914), # Hormuz 3
        pt(25, -75.0, 179.0),   # isolated middle spike
        pt(30, 26.033, 55.916), # Hormuz 4
        pt(35, 89.0, -179.0),   # trailing spike at end
    ]

    clean, rejected = despike_positions(track)
    assert [p.latitude for p in clean] == [26.03, 26.031, 26.032, 26.033]
    assert len(rejected) == 4


from sqlalchemy.exc import DBAPIError

from app.utils.geo import haversine_km


def test_haversine_antipodal_and_naive_aware_datetimes():
    # Exact antipodal points must not raise ValueError: math domain error
    d = haversine_km(26.0, 56.0, -26.0, -124.0)
    assert d > 19000

    naive_t0 = datetime(2026, 11, 22, 18, 0, 0)
    aware_t1 = datetime(2026, 11, 22, 19, 0, 0, tzinfo=timezone.utc)
    assert is_plausible_move(26.0, 56.0, naive_t0, 26.2, 56.0, aware_t1) is True


def _make_nested_cm():
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=None)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


@pytest.mark.asyncio
async def test_cleanup_corrupted_positions_deletes_spikes_and_repairs_vessel():
    t0 = datetime(2026, 11, 22, 12, 0, 0, tzinfo=timezone.utc)

    vessel = SimpleNamespace(
        mmsi=352002032,
        last_lat=-82.5,
        last_lon=-175.0,
        last_seen=t0 + timedelta(minutes=20),
        last_speed=0.0,
        last_course=0.0,
        last_source="aisstream",
    )
    rows = [
        SimpleNamespace(mmsi=352002032, time=t0, latitude=26.03, longitude=55.91, speed=1.0, course=180.0, source="openwaters:aishub"),
        SimpleNamespace(mmsi=352002032, time=t0 + timedelta(minutes=10), latitude=26.0332, longitude=55.9168, speed=2.5, course=212.2, source="openwaters:aishub"),
        SimpleNamespace(mmsi=352002032, time=t0 + timedelta(minutes=20), latitude=-82.5, longitude=-175.0, speed=0.0, course=0.0, source="aisstream"),
    ]

    def make_vessel_result(items):
        res = MagicMock()
        res.scalars.return_value.all.return_value = items
        return res

    def make_pos_result(items):
        res = MagicMock()
        res.all.return_value = items
        return res

    # Dry run first: nothing deleted or mutated
    db_dry = MagicMock()
    db_dry.execute = AsyncMock(side_effect=[make_vessel_result([vessel]), make_pos_result(rows)])
    db_dry.commit = AsyncMock()
    dry_summary = await cleanup_corrupted_positions(db_dry, mmsi=352002032, dry_run=True)
    assert dry_summary["positions_deleted"] == 1
    assert dry_summary["positions_skipped_compressed"] == 0
    assert dry_summary["vessels_repaired"] == 1
    assert vessel.last_lat == -82.5
    db_dry.commit.assert_not_awaited()

    # Real run: deletes 1 spike row and repairs vessel.last_lat/last_lon to latest clean Hormuz position
    db_real = MagicMock()
    db_real.begin_nested = MagicMock(side_effect=_make_nested_cm)
    db_real.execute = AsyncMock(side_effect=[make_vessel_result([vessel]), make_pos_result(rows), MagicMock()])
    db_real.commit = AsyncMock()
    real_summary = await cleanup_corrupted_positions(db_real, mmsi=352002032, dry_run=False)
    assert real_summary["positions_deleted"] == 1
    assert real_summary["positions_skipped_compressed"] == 0
    assert real_summary["vessels_repaired"] == 1
    assert vessel.last_lat == pytest.approx(26.0332)
    assert vessel.last_lon == pytest.approx(55.9168)
    assert vessel.last_seen == t0 + timedelta(minutes=10)
    db_real.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_handles_timescaledb_compressed_chunk_errors_gracefully():
    t0 = datetime(2026, 11, 22, 12, 0, 0, tzinfo=timezone.utc)
    vessel = SimpleNamespace(
        mmsi=352002032,
        last_lat=-82.5,
        last_lon=-175.0,
        last_seen=t0 + timedelta(minutes=30),
        last_speed=0.0,
        last_course=0.0,
        last_source="aisstream",
    )
    rows = [
        SimpleNamespace(mmsi=352002032, time=t0, latitude=26.03, longitude=55.91, speed=1.0, course=180.0, source="openwaters:aishub"),
        SimpleNamespace(mmsi=352002032, time=t0 + timedelta(minutes=10), latitude=0.0, longitude=0.0, speed=0.0, course=0.0, source="aisstream"),
        SimpleNamespace(mmsi=352002032, time=t0 + timedelta(minutes=20), latitude=26.0332, longitude=55.9168, speed=2.5, course=212.2, source="openwaters:aishub"),
        SimpleNamespace(mmsi=352002032, time=t0 + timedelta(minutes=30), latitude=-82.5, longitude=-175.0, speed=0.0, course=0.0, source="aisstream"),
    ]

    v_res = MagicMock()
    v_res.scalars.return_value.all.return_value = [vessel]
    p_res = MagicMock()
    p_res.all.return_value = rows

    compressed_err = DBAPIError("DELETE", {}, Exception("cannot update/delete tuples in a compressed chunk"))

    # Batch delete fails (compressed chunk) -> falls back row-by-row:
    # row 1 fails (compressed), row 2 succeeds (uncompressed)
    db = MagicMock()
    db.begin_nested = MagicMock(side_effect=_make_nested_cm)
    db.execute = AsyncMock(side_effect=[v_res, p_res, compressed_err, compressed_err, MagicMock()])
    db.commit = AsyncMock()

    summary = await cleanup_corrupted_positions(db, mmsi=352002032, dry_run=False)
    assert summary["positions_deleted"] == 1
    assert summary["positions_skipped_compressed"] == 1
    assert summary["vessels_repaired"] == 1
    assert vessel.last_lat == pytest.approx(26.0332)
    assert vessel.last_lon == pytest.approx(55.9168)
    db.commit.assert_awaited_once()
