"""Unit tests for AIS ingestion filtering, pending buffer replay, and Open Waters normalization."""

import pytest

from app.services import ais_ingestion


@pytest.fixture(autouse=True)
def reset_ingestion_state(monkeypatch):
    """Reset module-level caches between tests and stub WebSocket broadcast."""
    ais_ingestion._known_tankers.clear()
    ais_ingestion._known_non_tankers.clear()
    ais_ingestion._pending_positions.clear()
    ais_ingestion._vessel_batch.clear()
    ais_ingestion._position_batch.clear()
    ais_ingestion._last_saved_positions.clear()
    ais_ingestion._last_vessel_db_updates.clear()
    ais_ingestion._last_ui_updates.clear()
    ais_ingestion._last_seen_timestamps.clear()
    ais_ingestion._known_drafts.clear()

    broadcasts = []

    async def fake_broadcast(payload):
        broadcasts.append(payload)

    monkeypatch.setattr(ais_ingestion.manager, "broadcast", fake_broadcast)
    return broadcasts


def test_normalize_openwaters_control_frames():
    assert ais_ingestion._normalize_openwaters_event({"type": "welcome", "limits": {}}) is None
    assert ais_ingestion._normalize_openwaters_event({"type": "ack"}) is None
    assert ais_ingestion._normalize_openwaters_event({"type": "error", "error": "rate limit"}) is None


def test_normalize_openwaters_position_event():
    event = {
        "type": "event",
        "msg_type": "PositionReport",
        "mmsi": 470123456,
        "lat": 26.55,
        "lon": 56.45,
        "time": "2026-11-15T12:00:00Z",
        "source": "aishub",
        "message": {
            "UserID": 470123456,
            "Latitude": 26.55,
            "Longitude": 56.45,
            "Sog": 12.4,
            "Cog": 180.0,
            "TrueHeading": 181,
            "NavigationalStatus": 0,
        },
    }
    normalized = ais_ingestion._normalize_openwaters_event(event)
    assert normalized is not None
    assert normalized["MessageType"] == "PositionReport"
    assert normalized["MetaData"]["MMSI"] == 470123456
    assert normalized["MetaData"]["latitude"] == 26.55
    assert normalized["MetaData"]["Source"] == "openwaters:aishub"
    assert normalized["Message"]["PositionReport"]["Sog"] == 12.4


@pytest.mark.asyncio
async def test_pending_position_replayed_when_static_data_confirms_tanker(reset_ingestion_state):
    mmsi = 538009999
    pos_msg = {
        "MessageType": "PositionReport",
        "MetaData": {
            "MMSI": mmsi,
            "ShipName": "HORMUZ TANKER",
            "latitude": 26.5,
            "longitude": 56.5,
            "time_utc": "2026-11-15T12:00:00Z",
        },
        "Message": {
            "PositionReport": {
                "UserID": mmsi,
                "Latitude": 26.5,
                "Longitude": 56.5,
                "Sog": 11.2,
                "Cog": 95.0,
                "TrueHeading": 95,
                "NavigationalStatus": 0,
            }
        },
    }

    # Position arrives first without ShipType -> buffered, not dropped
    await ais_ingestion._process_message(pos_msg)
    assert mmsi in ais_ingestion._pending_positions
    assert mmsi not in ais_ingestion._known_tankers
    assert len(reset_ingestion_state) == 0

    # Static data arrives confirming ship type 80 (Tanker) -> replays buffered position
    static_msg = {
        "MessageType": "ShipStaticData",
        "MetaData": {
            "MMSI": mmsi,
            "ShipName": "HORMUZ TANKER",
            "time_utc": "2026-11-15T12:01:00Z",
        },
        "Message": {
            "ShipStaticData": {
                "UserID": mmsi,
                "Type": 80,
                "Name": "HORMUZ TANKER",
                "ImoNumber": 9123456,
                "MaximumStaticDraught": 145,
            }
        },
    }
    await ais_ingestion._process_message(static_msg)

    assert mmsi in ais_ingestion._known_tankers
    assert mmsi not in ais_ingestion._pending_positions
    assert len(ais_ingestion._position_batch) == 1
    assert ais_ingestion._position_batch[0]["mmsi"] == mmsi
    assert ais_ingestion._position_batch[0]["latitude"] == 26.5
    assert len(reset_ingestion_state) == 1
    assert reset_ingestion_state[0]["mmsi"] == mmsi


@pytest.mark.asyncio
async def test_confirmed_non_tanker_clears_pending_and_is_ignored(reset_ingestion_state):
    mmsi = 470000111
    pos_msg = {
        "MessageType": "PositionReport",
        "MetaData": {"MMSI": mmsi, "ShipName": "CARGO SHIP", "time_utc": "2026-11-15T12:00:00Z"},
        "Message": {"PositionReport": {"UserID": mmsi, "Latitude": 26.4, "Longitude": 56.3, "Sog": 10.0}},
    }
    await ais_ingestion._process_message(pos_msg)
    assert mmsi in ais_ingestion._pending_positions

    static_msg = {
        "MessageType": "StaticDataReport",
        "MetaData": {"MMSI": mmsi, "ShipName": "CARGO SHIP", "time_utc": "2026-11-15T12:01:00Z"},
        "Message": {"StaticDataReport": {"UserID": mmsi, "ReportB": {"ShipType": 70}}},
    }
    await ais_ingestion._process_message(static_msg)

    assert mmsi in ais_ingestion._known_non_tankers
    assert mmsi not in ais_ingestion._known_tankers
    assert mmsi not in ais_ingestion._pending_positions
    assert len(ais_ingestion._position_batch) == 0
    assert len(reset_ingestion_state) == 0


@pytest.mark.asyncio
async def test_stale_position_from_slower_source_is_dropped(reset_ingestion_state):
    mmsi = 538007777
    ais_ingestion._known_tankers.add(mmsi)

    newer_msg = {
        "MessageType": "PositionReport",
        "MetaData": {"MMSI": mmsi, "ShipName": "TANKER A", "time_utc": "2026-11-16T12:01:00Z", "Source": "aisstream"},
        "Message": {"PositionReport": {"UserID": mmsi, "Latitude": 26.5, "Longitude": 56.5, "Sog": 12.0}},
    }
    older_msg = {
        "MessageType": "PositionReport",
        "MetaData": {"MMSI": mmsi, "ShipName": "TANKER A", "time_utc": "2026-11-16T12:00:00Z", "Source": "openwaters:aishub"},
        "Message": {"PositionReport": {"UserID": mmsi, "Latitude": 26.4, "Longitude": 56.4, "Sog": 11.5}},
    }

    await ais_ingestion._process_message(newer_msg)
    await ais_ingestion._process_message(older_msg)

    assert len(ais_ingestion._position_batch) == 1
    assert ais_ingestion._position_batch[0]["source"] == "aisstream"
    assert len(reset_ingestion_state) == 1
    assert reset_ingestion_state[0]["source"] == "aisstream"

