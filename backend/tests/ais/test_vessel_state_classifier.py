"""Unit tests for VesselStateClassifier pure state & cargo classification."""

from app.services.vessel_state_classifier import (
    classify_vessel_state,
    determine_cargo_status,
)


def test_determine_cargo_status_from_draft_bounds():
    assert determine_cargo_status(16.0, 9.0, 17.0) == "laden"
    assert determine_cargo_status(9.5, 9.0, 17.0) == "ballast"
    # Narrow draft range falls back to latest cargo event
    assert determine_cargo_status(10.0, 10.0, 10.3, "loading") == "laden"
    assert determine_cargo_status(10.0, 10.0, 10.3, "unloading") == "ballast"
    assert determine_cargo_status(None, None, None) == "unknown"


def test_classify_vessel_state_priority_order():
    # 1. Dark overrides everything
    state, port_id = classify_vessel_state(
        age_hours=7.0,
        speed=12.0,
        nav_status=0,
        cargo_status="laden",
        is_dark=False,
        in_active_sts=True,
        nearest_port_id=1,
        nearest_port_dist_km=2.0,
    )
    assert state == "dark" and port_id is None

    # 2. STS transfer overrides port/transit when not dark
    state, port_id = classify_vessel_state(
        age_hours=0.1,
        speed=0.5,
        nav_status=0,
        cargo_status="laden",
        is_dark=False,
        in_active_sts=True,
        nearest_port_id=1,
        nearest_port_dist_km=30.0,
    )
    assert state == "sts_transfer" and port_id is None

    # 3. At port (berthed <= 5km and moored or <= 0.5 kn)
    state, port_id = classify_vessel_state(
        age_hours=0.1,
        speed=0.2,
        nav_status=5,
        cargo_status="laden",
        is_dark=False,
        in_active_sts=False,
        nearest_port_id=42,
        nearest_port_dist_km=3.0,
    )
    assert state == "at_port" and port_id == 42

    # 4. Anchored waiting outside port (5-25km, <= 1.5 kn)
    state, port_id = classify_vessel_state(
        age_hours=0.1,
        speed=0.8,
        nav_status=1,
        cargo_status="laden",
        is_dark=False,
        in_active_sts=False,
        nearest_port_id=42,
        nearest_port_dist_km=12.0,
    )
    assert state == "anchored_waiting" and port_id == 42

    # 5. Floating storage (laden, stationary offshore >= 7 days)
    state, port_id = classify_vessel_state(
        age_hours=0.1,
        speed=0.3,
        nav_status=0,
        cargo_status="laden",
        is_dark=False,
        in_active_sts=False,
        nearest_port_id=42,
        nearest_port_dist_km=80.0,
        stationary_days=8.0,
    )
    assert state == "floating_storage" and port_id is None

    # 6. In transit laden
    state, port_id = classify_vessel_state(
        age_hours=0.1,
        speed=13.5,
        nav_status=0,
        cargo_status="laden",
        is_dark=False,
        in_active_sts=False,
        nearest_port_id=None,
        nearest_port_dist_km=None,
    )
    assert state == "in_transit_laden" and port_id is None

    # 7. In transit ballast
    state, port_id = classify_vessel_state(
        age_hours=0.1,
        speed=14.0,
        nav_status=0,
        cargo_status="ballast",
        is_dark=False,
        in_active_sts=False,
        nearest_port_id=None,
        nearest_port_dist_km=None,
    )
    assert state == "in_transit_ballast" and port_id is None

    # 8. Idle / drifting offshore < 7 days
    state, port_id = classify_vessel_state(
        age_hours=0.1,
        speed=0.5,
        nav_status=0,
        cargo_status="ballast",
        is_dark=False,
        in_active_sts=False,
        nearest_port_id=None,
        nearest_port_dist_km=None,
        stationary_days=2.0,
    )
    assert state == "idle_drifting" and port_id is None


async def test_classify_all_active_sts_uses_vessel_a_and_b_mmsi(monkeypatch):
    from datetime import datetime, timezone
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from app.services import vessel_state_classifier as vsc

    now = datetime.now(timezone.utc)
    vessel = SimpleNamespace(
        mmsi=111222333,
        last_seen=now,
        last_lat=25.0,
        last_lon=55.0,
        last_speed=0.4,
        draft=14.0,
        min_draft=9.0,
        max_draft=15.0,
        cargo_status=None,
        operational_state=None,
        state_since=None,
        current_port_id=None,
    )

    def make_result(rows):
        res = MagicMock()
        res.all.return_value = rows
        res.scalars.return_value.all.return_value = rows
        return res

    # Sequence of execute() calls in classify_all():
    # 1. ports, 2. vessels, 3. dark_mmsis, 4. sts_rows, 5. nav_rows, 6. cargo_rows, 7. open_rows
    execute_results = [
        make_result([]),
        make_result([vessel]),
        make_result([]),
        make_result([SimpleNamespace(vessel_a_mmsi=111222333, vessel_b_mmsi=444555666)]),
        make_result([]),
        make_result([]),
        make_result([]),
    ]

    session = MagicMock()
    session.execute = AsyncMock(side_effect=execute_results)
    session.add = MagicMock()
    session.commit = AsyncMock()

    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session)
    session_ctx.__aexit__ = AsyncMock(return_value=False)

    monkeypatch.setattr(vsc, "async_session", lambda: session_ctx)

    result = await vsc.VesselStateClassifier().classify_all()

    assert result == {"classified": 1, "transitions": 1}
    assert vessel.operational_state == "sts_transfer"
    assert vessel.current_port_id is None
    session.add.assert_called_once()
    session.commit.assert_awaited_once()


async def test_classify_all_ignores_stale_dark_gap_for_fresh_vessel(monkeypatch):
    """A vessel seen 1 minute ago must not stay 'dark' while awaiting DarkFleetDetector resolution."""
    from datetime import datetime, timedelta, timezone
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock
    import app.services.vessel_state_classifier as vsc

    now = datetime.now(timezone.utc)
    vessel = SimpleNamespace(
        mmsi=987654321,
        last_lat=26.2,
        last_lon=56.4,
        last_speed=11.0,
        last_seen=now - timedelta(minutes=1),
        draft=14.0,
        min_draft=9.0,
        max_draft=15.0,
        cargo_status=None,
        operational_state="dark",
        state_since=now - timedelta(hours=8),
        current_port_id=None,
    )

    def make_result(rows):
        res = MagicMock()
        res.all.return_value = rows
        res.scalars.return_value.all.return_value = rows
        return res

    # Vessel still appears in dark_mmsis (unresolved AISGap), but last_seen is 1m ago
    execute_results = [
        make_result([]),
        make_result([vessel]),
        make_result([987654321]),
        make_result([]),
        make_result([]),
        make_result([]),
        make_result([]),
    ]

    session = MagicMock()
    session.execute = AsyncMock(side_effect=execute_results)
    session.add = MagicMock()
    session.commit = AsyncMock()

    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session)
    session_ctx.__aexit__ = AsyncMock(return_value=False)

    monkeypatch.setattr(vsc, "async_session", lambda: session_ctx)

    result = await vsc.VesselStateClassifier().classify_all()

    assert result == {"classified": 1, "transitions": 1}
    assert vessel.operational_state == "in_transit_laden"


