"""Unit tests for authenticated read-only /api/debug/* endpoints."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api import debug


def _make_nested_cm():
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=None)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def test_require_debug_token_disabled_when_env_unset(monkeypatch):
    monkeypatch.setattr(debug.settings, "ADMIN_DEBUG_TOKEN", "")
    with pytest.raises(HTTPException) as exc:
        debug.require_debug_token(authorization="Bearer anything", x_debug_token=None)
    assert exc.value.status_code == 503


def test_require_debug_token_validates_bearer_and_header(monkeypatch):
    monkeypatch.setattr(debug.settings, "ADMIN_DEBUG_TOKEN", "super-secret-token")

    # Missing or wrong token -> 401
    with pytest.raises(HTTPException) as exc:
        debug.require_debug_token(authorization=None, x_debug_token=None)
    assert exc.value.status_code == 401

    with pytest.raises(HTTPException) as exc:
        debug.require_debug_token(authorization="Bearer wrong", x_debug_token=None)
    assert exc.value.status_code == 401

    # Valid Bearer and X-Debug-Token -> passes
    debug.require_debug_token(authorization="Bearer super-secret-token", x_debug_token=None)
    debug.require_debug_token(authorization=None, x_debug_token="super-secret-token")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT mmsi, name FROM vessels LIMIT 5",
        "  SELECT count(*) FROM vessel_positions;  ",
        "WITH recent AS (SELECT * FROM vessels) SELECT * FROM recent",
        "EXPLAIN SELECT * FROM vessels WHERE mmsi = 352002032",
        "SELECT * FROM vessels WHERE name = 'DELETE ME'",  # forbidden word inside string literal is fine
    ],
)
def test_validate_readonly_sql_accepts_safe_queries(sql):
    cleaned = debug.validate_readonly_sql(sql)
    assert not cleaned.endswith(";")


@pytest.mark.parametrize(
    "bad_sql",
    [
        "",
        "   ",
        "DELETE FROM vessels WHERE mmsi = 1",
        "UPDATE vessels SET name = 'x'",
        "DROP TABLE vessels",
        "INSERT INTO vessels (mmsi) VALUES (1)",
        "SELECT 1; DROP TABLE vessels",
        "WITH d AS (DELETE FROM vessels RETURNING *) SELECT * FROM d",
        "SET statement_timeout = 0",
        "TRUNCATE vessel_positions",
    ],
)
def test_validate_readonly_sql_rejects_writes_and_multistatement(bad_sql):
    with pytest.raises(HTTPException) as exc:
        debug.validate_readonly_sql(bad_sql)
    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_debug_readonly_query_enforces_readonly_and_truncation():
    ts = datetime(2026, 11, 23, 12, 0, 0, tzinfo=timezone.utc)
    query_res = MagicMock()
    query_res.keys.return_value = ["mmsi", "last_seen"]
    # limit=2 -> fetchmany(3) returns 3 rows -> truncated=True, 2 returned
    query_res.fetchmany.return_value = [
        (352002032, ts),
        (111222333, ts),
        (444555666, ts),
    ]

    db = MagicMock()
    db.begin_nested = MagicMock(side_effect=_make_nested_cm)
    db.execute = AsyncMock(side_effect=[MagicMock(), MagicMock(), query_res])

    resp = await debug.debug_readonly_query(
        debug.DebugQueryRequest(sql="SELECT mmsi, last_seen FROM vessels", limit=2),
        db=db,
    )

    assert resp["columns"] == ["mmsi", "last_seen"]
    assert resp["row_count"] == 2
    assert resp["truncated"] is True
    assert resp["rows"][0] == {"mmsi": 352002032, "last_seen": ts.isoformat()}

    # Verify Postgres read-only + timeout statements were issued
    executed_sqls = [str(call.args[0]) for call in db.execute.await_args_list]
    assert "SET LOCAL TRANSACTION READ ONLY" in executed_sqls[0]
    assert "SET LOCAL statement_timeout" in executed_sqls[1]


@pytest.mark.asyncio
async def test_debug_vessel_returns_raw_and_despiked_summary():
    t0 = datetime(2026, 10, 22, 3, 21, 42, tzinfo=timezone.utc)
    t1 = datetime(2026, 10, 29, 2, 39, 46, tzinfo=timezone.utc)
    t2 = datetime(2026, 11, 22, 15, 34, 21, tzinfo=timezone.utc)

    vessel = SimpleNamespace(
        mmsi=352002032,
        imo=9358814,
        name="INTAN GLORY",
        ship_type=80,
        flag="PA",
        destination="MINA SAQR",
        last_seen=t2,
        last_lat=26.0332,
        last_lon=55.9168,
        last_speed=2.5,
        last_course=212.2,
        last_source="hormuz_now",
        operational_state="anchored_waiting",
        draft=4.6,
        dwt=11404.0,
    )
    pos_rows = [
        SimpleNamespace(time=t0, latitude=25.4569, longitude=55.2293, speed=0.1, course=100.0, source=None),
        SimpleNamespace(time=t1, latitude=57.3144, longitude=11.5166, speed=0.0, course=0.0, source=None),
        SimpleNamespace(time=t2, latitude=26.0332, longitude=55.9168, speed=2.5, course=212.2, source="openwaters:aishub"),
    ]

    v_res = MagicMock()
    v_res.scalar_one_or_none.return_value = vessel
    p_res = MagicMock()
    p_res.all.return_value = pos_rows
    empty_res = MagicMock()
    empty_res.scalars.return_value.all.return_value = []

    db = MagicMock()
    db.execute = AsyncMock(side_effect=[v_res, p_res, empty_res, empty_res, empty_res])

    resp = await debug.debug_vessel(mmsi=352002032, limit=200, db=db)
    assert resp["vessel"]["name"] == "INTAN GLORY"
    assert resp["positions_summary"]["raw_count"] == 3
    assert resp["positions_summary"]["clean_count"] == 2
    assert resp["positions_summary"]["rejected_count"] == 1
    assert resp["positions_summary"]["rejected_sample"][0]["lat"] == pytest.approx(57.3144)
