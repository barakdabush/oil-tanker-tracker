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
