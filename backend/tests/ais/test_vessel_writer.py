"""Unit tests for centralized vessel_writer ordering and deadlock retries."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import DBAPIError

from app.services import ais_ingestion, vessel_state_classifier as vsc, vessel_writer


def _make_deadlock_dbapi_error() -> DBAPIError:
    orig = asyncpg.exceptions.DeadlockDetectedError("deadlock detected")
    return DBAPIError("INSERT INTO vessels ...", {}, orig)


def test_is_deadlock_error_detection():
    assert vessel_writer.is_deadlock_error(
        asyncpg.exceptions.DeadlockDetectedError("deadlock detected")
    )
    assert vessel_writer.is_deadlock_error(_make_deadlock_dbapi_error())
    other_pg_err = RuntimeError("unique violation")
    other_pg_err.sqlstate = "23505"  # type: ignore[attr-defined]
    assert vessel_writer.is_deadlock_error(other_pg_err) is False
    assert vessel_writer.is_deadlock_error(RuntimeError("connection reset")) is False


async def test_upsert_vessels_and_positions_sorts_by_mmsi_and_time():
    now = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
    t1 = now - timedelta(minutes=10)
    t2 = now - timedelta(minutes=5)

    executed_stmts = []

    session = MagicMock()
    session.execute = AsyncMock(side_effect=lambda stmt: executed_stmts.append(stmt))

    vessels = [
        {"mmsi": 300, "name": "SHIP C", "last_seen": now},
        {"mmsi": 100, "name": "SHIP A", "draft": 12.5, "last_seen": now},
        {"mmsi": 200, "name": "SHIP B", "last_seen": now},
    ]
    positions = [
        {"mmsi": 200, "time": t2, "latitude": 25.0, "longitude": 55.0},
        {"mmsi": 100, "time": t2, "latitude": 24.0, "longitude": 54.0},
        {"mmsi": 100, "time": t1, "latitude": 23.9, "longitude": 53.9},
    ]

    v_count, p_count = await vessel_writer.upsert_vessels_and_positions(
        session, vessels, positions, now=now
    )

    assert (v_count, p_count) == (3, 3)
    assert len(executed_stmts) == 2

    # Verify vessels INSERT VALUES are in ascending MMSI order (100, 200, 300)
    v_compiled = executed_stmts[0].compile(dialect=postgresql.dialect())
    mmsi_params = [
        v_compiled.params[k]
        for k in v_compiled.params
        if k == "mmsi" or k.startswith("mmsi_m")
    ]
    assert mmsi_params == [100, 200, 300]

    # Verify positions INSERT VALUES are in ascending (mmsi, time) order
    p_compiled = executed_stmts[1].compile(dialect=postgresql.dialect())
    p_mmsis = [
        p_compiled.params[k]
        for k in p_compiled.params
        if k == "mmsi" or k.startswith("mmsi_m")
    ]
    p_times = [
        p_compiled.params[k]
        for k in p_compiled.params
        if k == "time" or k.startswith("time_m")
    ]
    assert list(zip(p_mmsis, p_times)) == [(100, t1), (100, t2), (200, t2)]


async def test_flush_vessels_and_positions_retries_deadlock_with_fresh_session(monkeypatch):
    monkeypatch.setattr(vessel_writer.asyncio, "sleep", AsyncMock())

    sessions_created = []

    def session_factory():
        attempt_idx = len(sessions_created) + 1
        session = MagicMock()
        if attempt_idx < 3:
            session.execute = AsyncMock(side_effect=_make_deadlock_dbapi_error())
        else:
            session.execute = AsyncMock()
        session.commit = AsyncMock()
        sessions_created.append(session)

        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    now = datetime.now(timezone.utc)
    counts = await vessel_writer.flush_vessels_and_positions(
        session_factory,
        [{"mmsi": 222, "last_seen": now}, {"mmsi": 111, "last_seen": now}],
        [],
        now=now,
        max_attempts=3,
    )

    assert counts == (2, 0)
    assert len(sessions_created) == 3
    sessions_created[0].commit.assert_not_awaited()
    sessions_created[1].commit.assert_not_awaited()
    sessions_created[2].commit.assert_awaited_once()


async def test_flush_vessels_and_positions_raises_non_deadlock_immediately(monkeypatch):
    sleep_mock = AsyncMock()
    monkeypatch.setattr(vessel_writer.asyncio, "sleep", sleep_mock)

    def session_factory():
        session = MagicMock()
        session.execute = AsyncMock(side_effect=ValueError("bad value"))
        session.commit = AsyncMock()
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    with pytest.raises(ValueError, match="bad value"):
        await vessel_writer.flush_vessels_and_positions(
            session_factory,
            [{"mmsi": 111, "last_seen": datetime.now(timezone.utc)}],
            [],
            max_attempts=3,
        )

    sleep_mock.assert_not_awaited()


async def test_classify_all_orders_by_mmsi_and_prefetches_open_history_once(monkeypatch):
    now = datetime.now(timezone.utc)
    v1 = SimpleNamespace(
        mmsi=111,
        last_seen=now,
        last_lat=25.0,
        last_lon=55.0,
        last_speed=12.0,
        draft=14.0,
        min_draft=9.0,
        max_draft=15.0,
        cargo_status=None,
        operational_state=None,
        state_since=None,
        current_port_id=None,
    )
    v2 = SimpleNamespace(
        mmsi=222,
        last_seen=now,
        last_lat=26.0,
        last_lon=56.0,
        last_speed=13.0,
        draft=10.0,
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

    # Exactly 7 execute() calls even with 2 transitioning vessels (single prefetched open_history query)
    execute_results = [
        make_result([]),          # 1. ports
        make_result([v1, v2]),    # 2. vessels
        make_result([]),          # 3. dark_mmsis
        make_result([]),          # 4. sts_rows
        make_result([]),          # 5. nav_rows
        make_result([]),          # 6. cargo_rows
        make_result([]),          # 7. open_history_rows (prefetched for both MMSIs)
    ]

    captured_stmts = []

    async def fake_execute(stmt, *args, **kwargs):
        captured_stmts.append(stmt)
        return execute_results[len(captured_stmts) - 1]

    session = MagicMock()
    session.execute = AsyncMock(side_effect=fake_execute)
    session.add = MagicMock()
    session.commit = AsyncMock()

    session_ctx = MagicMock()
    session_ctx.__aenter__ = AsyncMock(return_value=session)
    session_ctx.__aexit__ = AsyncMock(return_value=False)

    monkeypatch.setattr(vsc, "async_session", lambda: session_ctx)

    result = await vsc.VesselStateClassifier().classify_all()

    assert result == {"classified": 2, "transitions": 2}
    assert len(captured_stmts) == 7
    vessels_sql = str(captured_stmts[1].compile(dialect=postgresql.dialect()))
    assert "ORDER BY vessels.mmsi ASC" in vessels_sql
    assert session.add.call_count == 2
    session.commit.assert_awaited_once()


async def test_flush_batch_uses_centralized_writer(monkeypatch):
    mock_flush = AsyncMock(return_value=(2, 1))
    monkeypatch.setattr(ais_ingestion, "flush_vessels_and_positions", mock_flush)

    now = datetime.now(timezone.utc)
    ais_ingestion._vessel_batch = {
        200: {"mmsi": 200, "last_seen": now},
        100: {"mmsi": 100, "last_seen": now},
    }
    ais_ingestion._position_batch = [
        {"mmsi": 200, "time": now, "latitude": 25.0, "longitude": 55.0}
    ]

    await ais_ingestion.flush_batch()

    mock_flush.assert_awaited_once()
    assert ais_ingestion._vessel_batch == {}
    assert ais_ingestion._position_batch == []
