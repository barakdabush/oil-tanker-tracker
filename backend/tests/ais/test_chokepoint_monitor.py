"""Tests for hybrid chokepoint transit detection (polygon + gate-line crossing)."""

from datetime import datetime, timedelta, timezone

import pytest
from shapely import wkt as shapely_wkt
from shapely.geometry import Polygon

from app.services.chokepoint_monitor import (
    DEFAULT_GATE_LINES_WKT,
    ChokepointGeometry,
    TrackPoint,
    detect_segment_crossing,
    evaluate_vessel_track,
    replay_historical_track,
)


HORMUZ_POLYGON = Polygon([
    (56.0, 26.0),
    (56.5, 26.0),
    (57.0, 26.5),
    (56.8, 27.0),
    (56.3, 27.0),
    (55.8, 26.5),
    (56.0, 26.0),
])


def _make_hormuz() -> ChokepointGeometry:
    return ChokepointGeometry(
        id=1,
        name="Strait of Hormuz",
        congestion_threshold=30,
        polygon=HORMUZ_POLYGON,
        gate_line=shapely_wkt.loads(DEFAULT_GATE_LINES_WKT["Strait of Hormuz"]),
    )


def test_hormuz_dark_transit_outbound_around_musandam():
    """Tanker goes dark near Dubai (Persian Gulf) and reappears off Fujairah (Gulf of Oman).

    The straight-line chord cuts south of the Hormuz polygon across the Musandam
    Peninsula, so polygon-only intersection would miss it — the gate line catches it.
    """
    cp = _make_hormuz()
    t0 = datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=18)

    p1 = TrackPoint(time=t0, lat=25.30, lon=55.20, draft=14.2)   # Off Dubai (Persian Gulf)
    p2 = TrackPoint(time=t1, lat=25.10, lon=56.60, draft=14.2)   # Off Fujairah (Gulf of Oman)

    match = detect_segment_crossing(p1, p2, cp)
    assert match is not None
    assert match.method == "gate_crossing"
    assert match.direction == "eastbound"
    assert t0 < match.crossing_time < t1
    assert 0.0 < match.fraction < 1.0


def test_hormuz_dark_transit_inbound_from_gulf_of_oman():
    """Tanker goes dark in Gulf of Oman and reappears inside the Persian Gulf."""
    cp = _make_hormuz()
    t0 = datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=24)

    p1 = TrackPoint(time=t0, lat=24.80, lon=58.00)  # Gulf of Oman
    p2 = TrackPoint(time=t1, lat=26.40, lon=53.80)  # Inside Persian Gulf

    match = detect_segment_crossing(p1, p2, cp)
    assert match is not None
    assert match.method == "gate_crossing"
    assert match.direction == "westbound"
    assert t0 < match.crossing_time < t1


def test_hormuz_non_crossing_vessels_not_flagged():
    """Vessels staying strictly inside Persian Gulf or strictly in Gulf of Oman must not trigger."""
    cp = _make_hormuz()
    t0 = datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=10)

    # Sailing strictly inside Persian Gulf (Ras Tanura -> Dubai)
    p_gulf_1 = TrackPoint(time=t0, lat=26.64, lon=50.16)
    p_gulf_2 = TrackPoint(time=t1, lat=25.30, lon=55.20)
    assert detect_segment_crossing(p_gulf_1, p_gulf_2, cp) is None

    # Sailing strictly in Gulf of Oman / Arabian Sea (Muscat -> Karachi)
    p_oman_1 = TrackPoint(time=t0, lat=23.60, lon=58.60)
    p_oman_2 = TrackPoint(time=t1, lat=24.80, lon=66.50)
    assert detect_segment_crossing(p_oman_1, p_oman_2, cp) is None

    # Sailing in Arabian Sea well south of Oman coast (never enters Hormuz)
    p_sea_1 = TrackPoint(time=t0, lat=20.00, lon=54.00)
    p_sea_2 = TrackPoint(time=t1, lat=21.00, lon=60.00)
    assert detect_segment_crossing(p_sea_1, p_sea_2, cp) is None


def test_gps_spoofing_and_excessive_gap_rejected():
    """Unrealistic speed (teleport/spoofing) or >120h gaps must be rejected."""
    cp = _make_hormuz()
    t0 = datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc)

    # Teleport 400 km in 10 minutes (~1,300 knots) -> rejected
    p1 = TrackPoint(time=t0, lat=26.50, lon=54.50)
    p2 = TrackPoint(time=t0 + timedelta(minutes=10), lat=25.50, lon=58.50)
    assert detect_segment_crossing(p1, p2, cp) is None

    # Excessive time gap (10 days > 120h) -> rejected
    p3 = TrackPoint(time=t0 + timedelta(days=10), lat=25.50, lon=58.50)
    assert detect_segment_crossing(p1, p3, cp, max_gap_hours=120.0) is None

    # Excessive distance (> 1500 km, e.g. Kuwait to India) -> rejected
    p_far_1 = TrackPoint(time=t0, lat=29.00, lon=48.50)
    p_far_2 = TrackPoint(time=t0 + timedelta(hours=100), lat=19.00, lon=72.00)
    assert detect_segment_crossing(p_far_1, p_far_2, cp, max_distance_km=1500.0) is None


def test_normal_live_ais_transit_not_double_counted():
    """A vessel with normal AIS pings entering, crossing gate inside polygon, and exiting
    must produce exactly 1 transit (not 2).
    """
    cp = _make_hormuz()
    active: dict[tuple[int, int], datetime] = {}
    recent: dict[tuple[int, int], datetime] = {}
    t0 = datetime(2026, 11, 21, 10, 0, tzinfo=timezone.utc)

    # Scan 1: outside west -> inside polygon (west of gate)
    p1 = TrackPoint(time=t0, lat=26.50, lon=55.50, course=90.0)
    p2 = TrackPoint(time=t0 + timedelta(minutes=20), lat=26.50, lon=56.10, course=90.0)
    res1 = evaluate_vessel_track(
        mmsi=123456789,
        vessel_draft=15.0,
        vessel_course=90.0,
        track=[p1, p2],
        cp=cp,
        active_transits=active,
        recent_transit_times=recent,
        now=p2.time,
        scan_since=t0 - timedelta(minutes=5),
    )
    assert len(res1.new_transits) == 1
    assert res1.new_transits[0]["detection_method"] == "polygon"
    assert res1.new_transits[0]["exit_time"] is None
    assert (123456789, cp.id) in active

    # Scan 2: crosses gate while still inside polygon (~50 km in 100 min = ~16 knots)
    p3 = TrackPoint(time=t0 + timedelta(minutes=120), lat=26.50, lon=56.60, course=90.0)
    res2 = evaluate_vessel_track(
        mmsi=123456789,
        vessel_draft=15.0,
        vessel_course=90.0,
        track=[p2, p3],
        cp=cp,
        active_transits=active,
        recent_transit_times=recent,
        now=p3.time,
        scan_since=p2.time,
    )
    assert len(res2.new_transits) == 0  # Must NOT double count!
    assert len(res2.closed_transits) == 0
    assert len(res2.direction_updates) == 1
    assert res2.direction_updates[0]["direction"] == "eastbound"

    # Scan 3: exits polygon to the east
    p4 = TrackPoint(time=t0 + timedelta(minutes=260), lat=26.20, lon=57.30, course=105.0)
    res3 = evaluate_vessel_track(
        mmsi=123456789,
        vessel_draft=15.0,
        vessel_course=105.0,
        track=[p3, p4],
        cp=cp,
        active_transits=active,
        recent_transit_times=recent,
        now=p4.time,
        scan_since=p3.time,
    )
    assert len(res3.new_transits) == 0
    assert len(res3.closed_transits) == 1
    assert res3.closed_transits[0]["transit_duration_min"] == 240.0
    assert (123456789, cp.id) not in active


def test_partial_dark_entry_reappears_inside_polygon():
    """Vessel broadcasts before Hormuz (east), goes dark, and reappears INSIDE polygon
    on the west side of the gate -> opens 1 transit, then closes cleanly on exit.
    """
    cp = _make_hormuz()
    active: dict[tuple[int, int], datetime] = {}
    recent: dict[tuple[int, int], datetime] = {}
    t0 = datetime(2026, 11, 21, 8, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=5)

    p1 = TrackPoint(time=t0, lat=25.80, lon=57.20)   # Outside east (Gulf of Oman)
    p2 = TrackPoint(time=t1, lat=26.50, lon=56.10)   # Inside polygon, west of gate

    res1 = evaluate_vessel_track(
        mmsi=987654321,
        vessel_draft=12.5,
        vessel_course=290.0,
        track=[p1, p2],
        cp=cp,
        active_transits=active,
        recent_transit_times=recent,
        now=t1,
        scan_since=t1 - timedelta(minutes=30),
    )
    assert len(res1.new_transits) == 1
    assert res1.new_transits[0]["detection_method"] == "gate_crossing"
    assert res1.new_transits[0]["direction"] == "westbound"
    assert res1.new_transits[0]["exit_time"] is None  # Still inside polygon!
    assert (987654321, cp.id) in active

    # Later exits polygon into Persian Gulf
    t2 = t1 + timedelta(hours=1)
    p3 = TrackPoint(time=t2, lat=26.60, lon=55.40)
    res2 = evaluate_vessel_track(
        mmsi=987654321,
        vessel_draft=12.5,
        vessel_course=280.0,
        track=[p2, p3],
        cp=cp,
        active_transits=active,
        recent_transit_times=recent,
        now=t2,
        scan_since=t1,
    )
    assert len(res2.new_transits) == 0
    assert len(res2.closed_transits) == 1
    assert (987654321, cp.id) not in active


def test_cooldown_prevents_jitter_duplicates():
    """Back-and-forth jitter across the gate line within cooldown window records only 1 transit."""
    cp = _make_hormuz()
    active: dict[tuple[int, int], datetime] = {}
    recent: dict[tuple[int, int], datetime] = {}
    t0 = datetime(2026, 11, 21, 10, 0, tzinfo=timezone.utc)

    p1 = TrackPoint(time=t0, lat=25.30, lon=55.50)
    p2 = TrackPoint(time=t0 + timedelta(hours=3), lat=25.20, lon=56.60)
    p3 = TrackPoint(time=t0 + timedelta(hours=5), lat=25.30, lon=55.50)

    res = evaluate_vessel_track(
        mmsi=555000111,
        vessel_draft=11.0,
        vessel_course=None,
        track=[p1, p2, p3],
        cp=cp,
        active_transits=active,
        recent_transit_times=recent,
        now=p3.time,
        scan_since=t0,
        cooldown_hours=12.0,
    )
    assert len(res.new_transits) == 1
    assert res.new_transits[0]["detection_method"] == "gate_crossing"
    assert res.new_transits[0]["exit_time"] is not None
    assert res.new_transits[0]["transit_duration_min"] is None


def test_polygon_segment_fallback_when_no_gate_line():
    """When a chokepoint has no gate_line, jumping across its polygon still detects a transit."""
    cp = ChokepointGeometry(
        id=99,
        name="Custom Strait",
        congestion_threshold=10,
        polygon=HORMUZ_POLYGON,
        gate_line=None,
    )
    t0 = datetime(2026, 11, 21, 10, 0, tzinfo=timezone.utc)
    p1 = TrackPoint(time=t0, lat=26.50, lon=55.50)
    p2 = TrackPoint(time=t0 + timedelta(hours=4), lat=26.50, lon=57.50)

    match = detect_segment_crossing(p1, p2, cp)
    assert match is not None
    assert match.method == "polygon_segment"
    assert match.direction == "eastbound"


@pytest.mark.parametrize(
    "name,p1_coords,p2_coords,expected_dir",
    [
        ("Strait of Hormuz", (25.30, 55.20), (25.10, 56.60), "eastbound"),
        ("Strait of Malacca", (3.20, 100.50), (1.20, 103.60), "eastbound"),
        ("Suez Canal", (31.35, 32.35), (29.85, 32.55), "southbound"),
        ("Bab el-Mandeb", (13.40, 42.90), (12.10, 44.20), "southbound"),
        ("Turkish Straits (Bosporus)", (41.35, 29.15), (40.85, 28.95), "southbound"),
        ("Danish Straits", (56.20, 11.20), (54.80, 11.50), "southbound"),
        ("Cape of Good Hope", (-35.00, 17.00), (-35.20, 20.50), "eastbound"),
        ("Panama Canal", (9.45, -79.95), (8.80, -79.50), "southbound"),
    ],
)
def test_all_eight_chokepoint_default_gate_lines(name, p1_coords, p2_coords, expected_dir):
    """Verify every default gate line catches a dark transit between realistic pre/post points."""
    gate_line = shapely_wkt.loads(DEFAULT_GATE_LINES_WKT[name])
    cp = ChokepointGeometry(
        id=1,
        name=name,
        congestion_threshold=20,
        polygon=None,
        gate_line=gate_line,
    )
    t0 = datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc)
    p1 = TrackPoint(time=t0, lat=p1_coords[0], lon=p1_coords[1])
    p2 = TrackPoint(time=t0 + timedelta(hours=24), lat=p2_coords[0], lon=p2_coords[1])

    match = detect_segment_crossing(p1, p2, cp)
    assert match is not None, f"Gate line missed realistic dark transit for {name}"
    assert match.method == "gate_crossing"
    assert match.direction == expected_dir


def test_historical_replay_round_trip_mixed_live_and_dark():
    """Replay a 5-day track with 1 live-AIS Hormuz outbound pass and 1 dark-AIS inbound return."""
    cp = _make_hormuz()
    day1 = datetime(2026, 11, 15, 6, 0, tzinfo=timezone.utc)
    day4 = day1 + timedelta(days=3)

    track = [
        # Day 1: Live AIS outbound through Hormuz polygon + gate
        TrackPoint(time=day1, lat=26.50, lon=55.50, course=90.0, draft=14.5),
        TrackPoint(time=day1 + timedelta(hours=2), lat=26.50, lon=56.10, course=90.0, draft=14.5),
        TrackPoint(time=day1 + timedelta(hours=4), lat=26.50, lon=56.60, course=90.0, draft=14.5),
        TrackPoint(time=day1 + timedelta(hours=6), lat=26.20, lon=57.30, course=105.0, draft=14.5),
        # Day 4: Dark AIS inbound return from Fujairah to Dubai (skips polygon completely)
        TrackPoint(time=day4, lat=25.10, lon=56.60, draft=9.5),
        TrackPoint(time=day4 + timedelta(hours=18), lat=25.30, lon=55.20, draft=9.5),
    ]

    transits = replay_historical_track(
        mmsi=412345678,
        vessel_draft=14.5,
        track=track,
        cp=cp,
    )
    assert len(transits) == 2

    # Transit 1: Live polygon pass
    assert transits[0]["detection_method"] == "polygon"
    assert transits[0]["direction"] == "eastbound"
    assert transits[0]["transit_duration_min"] == 240.0

    # Transit 2: Recovered dark-AIS gate crossing
    assert transits[1]["detection_method"] == "gate_crossing"
    assert transits[1]["direction"] == "westbound"
    assert transits[1]["transit_duration_min"] is None
    assert transits[1]["exit_time"] == transits[1]["entry_time"]


def test_historical_replay_additive_idempotency():
    """Additive backfill skips already-recorded transits while recovering missed dark crossings."""
    cp = _make_hormuz()
    day1 = datetime(2026, 11, 15, 6, 0, tzinfo=timezone.utc)
    day4 = day1 + timedelta(days=3)

    track = [
        # Day 1 live transit (already in DB)
        TrackPoint(time=day1, lat=26.50, lon=55.50, course=90.0),
        TrackPoint(time=day1 + timedelta(hours=2), lat=26.50, lon=56.10, course=90.0),
        TrackPoint(time=day1 + timedelta(hours=6), lat=26.20, lon=57.30, course=105.0),
        # Day 4 dark transit (previously missed)
        TrackPoint(time=day4, lat=25.10, lon=56.60),
        TrackPoint(time=day4 + timedelta(hours=18), lat=25.30, lon=55.20),
    ]

    # Simulate Day 1 transit already existing in DB
    existing_times = [day1 + timedelta(hours=2)]

    newly_found = replay_historical_track(
        mmsi=412345678,
        vessel_draft=12.0,
        track=track,
        cp=cp,
        existing_transit_times=existing_times,
    )
    assert len(newly_found) == 1
    assert newly_found[0]["detection_method"] == "gate_crossing"
    assert newly_found[0]["direction"] == "westbound"

    # Running a second time with the newly inserted timestamp finds 0 duplicates
    second_run = replay_historical_track(
        mmsi=412345678,
        vessel_draft=12.0,
        track=track,
        cp=cp,
        existing_transit_times=existing_times + [newly_found[0]["entry_time"]],
    )
    assert len(second_run) == 0


def test_extended_hormuz_suez_bab_long_dark_routes():
    """Extended inland gate lines catch southern Persian Gulf, East Med, and North Red Sea dark gaps."""
    t0 = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=72)

    hormuz = _make_hormuz()
    # Qatar (Ras Laffan) -> Muscat (previously missed when Hormuz gate stopped at 24.50N)
    m1 = detect_segment_crossing(
        TrackPoint(time=t0, lat=25.90, lon=51.60),
        TrackPoint(time=t1, lat=23.62, lon=58.59),
        hormuz,
    )
    assert m1 is not None and m1.method == "gate_crossing"

    # Abu Dhabi -> Sohar
    m2 = detect_segment_crossing(
        TrackPoint(time=t0, lat=24.50, lon=54.30),
        TrackPoint(time=t1, lat=24.45, lon=56.75),
        hormuz,
    )
    assert m2 is not None and m2.method == "gate_crossing"

    # Suez: Alexandria/Med -> Red Sea
    suez = ChokepointGeometry(
        id=3, name="Suez Canal", congestion_threshold=15, polygon=None,
        gate_line=shapely_wkt.loads(DEFAULT_GATE_LINES_WKT["Suez Canal"]),
    )
    m3 = detect_segment_crossing(
        TrackPoint(time=t0, lat=31.50, lon=30.50),
        TrackPoint(time=t1, lat=27.50, lon=34.00),
        suez,
    )
    assert m3 is not None and m3.method == "gate_crossing"

    # Bab el-Mandeb: Jeddah -> Aden
    bab = ChokepointGeometry(
        id=4, name="Bab el-Mandeb", congestion_threshold=20, polygon=None,
        gate_line=shapely_wkt.loads(DEFAULT_GATE_LINES_WKT["Bab el-Mandeb"]),
    )
    m4 = detect_segment_crossing(
        TrackPoint(time=t0, lat=21.00, lon=39.00),
        TrackPoint(time=t1, lat=12.50, lon=45.50),
        bab,
    )
    assert m4 is not None and m4.method == "gate_crossing"


def test_rejection_diagnostics_track_reasons():
    """rejected_counts records why segments crossing a gate line were filtered out."""
    cp = _make_hormuz()
    t0 = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)
    rejected: dict[str, int] = {}

    # Speed too high (teleport across Hormuz in 10 min)
    detect_segment_crossing(
        TrackPoint(time=t0, lat=26.50, lon=54.50),
        TrackPoint(time=t0 + timedelta(minutes=10), lat=25.50, lon=58.50),
        cp,
        rejected_counts=rejected,
    )
    assert rejected.get("speed_too_high") == 1

    # 20-day dark transit across Hormuz is accepted under 30d (720h) default
    m_20d = detect_segment_crossing(
        TrackPoint(time=t0, lat=26.50, lon=54.50),
        TrackPoint(time=t0 + timedelta(days=20), lat=25.50, lon=58.50),
        cp,
        rejected_counts=rejected,
    )
    assert m_20d is not None and m_20d.method == "gate_crossing"

    # Gap too long (35 days > 30d default)
    detect_segment_crossing(
        TrackPoint(time=t0, lat=26.50, lon=54.50),
        TrackPoint(time=t0 + timedelta(days=35), lat=25.50, lon=58.50),
        cp,
        rejected_counts=rejected,
    )
    assert rejected.get("gap_too_long") == 1


def test_backend_app_and_chokepoints_recalculate_route_import():
    """Regression test for #48: app.main and chokepoints router must import cleanly."""
    from app.main import app

    route_paths = {route.path for route in app.routes}
    assert "/api/chokepoints/recalculate" in route_paths


def test_hormuz_closed_basin_long_distance_transitions():
    """Tankers jumping between Persian Gulf and distant AIS boxes (Malacca, Bab el-Mandeb, Cape)
    MUST be detected as Hormuz transits via closed-basin transition, while cross-continental
    routes outside the Gulf (Turkey -> Malacca) must NOT false-positive.
    """
    from app.services.chokepoint_monitor import CLOSED_BASIN_CONFIG

    cfg = CLOSED_BASIN_CONFIG["Strait of Hormuz"]
    hormuz = ChokepointGeometry(
        id=1,
        name="Strait of Hormuz",
        congestion_threshold=30,
        polygon=HORMUZ_POLYGON,
        gate_line=shapely_wkt.loads(DEFAULT_GATE_LINES_WKT["Strait of Hormuz"]),
        basin_polygon=cfg["polygon"],
        basin_center=cfg["center"],
        basin_outbound_direction=cfg["outbound_direction"],
        basin_inbound_direction=cfg["inbound_direction"],
    )
    t0 = datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)

    # 1. Ras Tanura (Persian Gulf) -> Strait of Malacca (~5,700 km in 14 days)
    m_east = detect_segment_crossing(
        TrackPoint(time=t0, lat=26.64, lon=50.16),
        TrackPoint(time=t0 + timedelta(days=14), lat=2.00, lon=102.00),
        hormuz,
    )
    assert m_east is not None
    assert m_east.method == "basin_transition"
    assert m_east.direction == "eastbound"
    # Crossing timestamp should be early in the voyage (~10% of 14 days, near Hormuz)
    assert m_east.crossing_time < t0 + timedelta(days=3)

    # 2. Ras Tanura (Persian Gulf) -> Bab el-Mandeb (chord goes SW over Saudi Arabia, never touches 56E!)
    m_sw = detect_segment_crossing(
        TrackPoint(time=t0, lat=26.64, lon=50.16),
        TrackPoint(time=t0 + timedelta(days=8), lat=12.65, lon=43.30),
        hormuz,
    )
    assert m_sw is not None
    assert m_sw.method == "basin_transition"
    assert m_sw.direction == "eastbound"

    # 3. Inbound: Cape of Good Hope -> Ras Tanura (22 days)
    m_in = detect_segment_crossing(
        TrackPoint(time=t0, lat=-35.00, lon=18.50),
        TrackPoint(time=t0 + timedelta(days=22), lat=26.64, lon=50.16),
        hormuz,
    )
    assert m_in is not None
    assert m_in.method == "basin_transition"
    assert m_in.direction == "westbound"

    # 4. Cross-continental non-Gulf route (Turkish Straits -> Malacca) must NOT trigger Hormuz
    m_false = detect_segment_crossing(
        TrackPoint(time=t0, lat=41.12, lon=29.05),
        TrackPoint(time=t0 + timedelta(days=20), lat=2.00, lon=102.00),
        hormuz,
    )
    assert m_false is None

    # 5. GPS spoof (Ras Tanura -> Malacca in 1 hour) must still be rejected
    rejected: dict[str, int] = {}
    m_spoof = detect_segment_crossing(
        TrackPoint(time=t0, lat=26.64, lon=50.16),
        TrackPoint(time=t0 + timedelta(hours=1), lat=2.00, lon=102.00),
        hormuz,
        rejected_counts=rejected,
    )
    assert m_spoof is None
    assert rejected.get("speed_too_high") == 1
