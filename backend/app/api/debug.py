"""Authenticated read-only diagnostic & DB inspection endpoints for rapid debugging.

All endpoints are disabled (HTTP 503) unless ``ADMIN_DEBUG_TOKEN`` is configured
in the environment, and require either ``Authorization: Bearer <token>`` or
``X-Debug-Token: <token>``.
"""

from __future__ import annotations

import re
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import (
    AISGap,
    ChokepointTransit,
    SystemLog,
    Vessel,
    VesselPosition,
    VesselStateHistory,
)
from app.utils.geo import despike_positions

router = APIRouter()

_ALLOWED_START_KEYWORDS = {"SELECT", "WITH", "EXPLAIN"}
_FORBIDDEN_SQL_KEYWORDS = re.compile(
    r"\b("
    r"INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|COPY|"
    r"CALL|DO|SET|VACUUM|REINDEX|CLUSTER|LOCK|MERGE|EXECUTE|PREPARE"
    r")\b",
    re.IGNORECASE,
)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"--[^\n]*")
_STRING_LITERAL_RE = re.compile(r"'(?:''|[^'])*'")


def _serialize_value(val: Any) -> Any:
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, UUID):
        return str(val)
    if isinstance(val, (bytes, bytearray, memoryview)):
        return bytes(val).hex()
    return val


def require_debug_token(
    authorization: Optional[str] = Header(None),
    x_debug_token: Optional[str] = Header(None),
) -> None:
    """Enforce ADMIN_DEBUG_TOKEN authentication on all /api/debug/* endpoints."""
    expected = (settings.ADMIN_DEBUG_TOKEN or "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Debug API is disabled (ADMIN_DEBUG_TOKEN is not configured).",
        )

    provided = ""
    if authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    elif x_debug_token:
        provided = x_debug_token.strip()

    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid or missing debug token.")


def validate_readonly_sql(sql: str) -> str:
    """Validate that a SQL query is a single read-only SELECT/WITH/EXPLAIN statement."""
    cleaned = (sql or "").strip()
    if cleaned.endswith(";"):
        cleaned = cleaned[:-1].strip()
    if not cleaned:
        raise HTTPException(status_code=400, detail="SQL query cannot be empty.")

    # Strip comments and string literals for safe keyword/semicolon inspection
    scrubbed = _BLOCK_COMMENT_RE.sub(" ", cleaned)
    scrubbed = _LINE_COMMENT_RE.sub(" ", scrubbed)
    scrubbed_no_strings = _STRING_LITERAL_RE.sub("''", scrubbed)

    if ";" in scrubbed_no_strings:
        raise HTTPException(status_code=400, detail="Multiple SQL statements are not allowed.")

    tokens = scrubbed_no_strings.strip().split()
    if not tokens or tokens[0].upper() not in _ALLOWED_START_KEYWORDS:
        raise HTTPException(
            status_code=400,
            detail="Only read-only SELECT, WITH, or EXPLAIN queries are permitted.",
        )

    forbidden = _FORBIDDEN_SQL_KEYWORDS.search(scrubbed_no_strings)
    if forbidden:
        raise HTTPException(
            status_code=400,
            detail=f"Forbidden SQL keyword in read-only query: {forbidden.group(1).upper()}",
        )

    return cleaned


class DebugQueryRequest(BaseModel):
    sql: str = Field(..., min_length=1, max_length=8000)
    limit: int = Field(200, ge=1, le=500)


@router.get("/vessel/{mmsi}", dependencies=[Depends(require_debug_token)])
async def debug_vessel(
    mmsi: int,
    limit: int = Query(200, ge=1, le=2000, description="Max raw positions to return"),
    db: AsyncSession = Depends(get_db),
):
    """Full diagnostic snapshot for a vessel: metadata, raw vs despiked positions, transits, states, gaps."""
    vessel = (await db.execute(select(Vessel).where(Vessel.mmsi == mmsi))).scalar_one_or_none()
    if not vessel:
        raise HTTPException(status_code=404, detail=f"Vessel {mmsi} not found")

    pos_rows = list(
        (
            await db.execute(
                select(
                    VesselPosition.time,
                    VesselPosition.latitude,
                    VesselPosition.longitude,
                    VesselPosition.speed,
                    VesselPosition.course,
                    VesselPosition.source,
                )
                .where(VesselPosition.mmsi == mmsi)
                .order_by(VesselPosition.time.asc())
            )
        ).all()
    )
    clean_rows, rejected_rows = despike_positions(pos_rows)

    transits = list(
        (
            await db.execute(
                select(ChokepointTransit)
                .where(ChokepointTransit.mmsi == mmsi)
                .order_by(desc(ChokepointTransit.entry_time))
                .limit(25)
            )
        )
        .scalars()
        .all()
    )

    states = list(
        (
            await db.execute(
                select(VesselStateHistory)
                .where(VesselStateHistory.mmsi == mmsi)
                .order_by(desc(VesselStateHistory.started_at))
                .limit(25)
            )
        )
        .scalars()
        .all()
    )

    gaps = list(
        (
            await db.execute(
                select(AISGap)
                .where(AISGap.mmsi == mmsi)
                .order_by(desc(AISGap.gap_start))
                .limit(25)
            )
        )
        .scalars()
        .all()
    )

    recent_raw = pos_rows[-limit:]

    return {
        "vessel": {
            "mmsi": vessel.mmsi,
            "imo": vessel.imo,
            "name": vessel.name,
            "ship_type": vessel.ship_type,
            "flag": vessel.flag,
            "destination": vessel.destination,
            "last_seen": _serialize_value(vessel.last_seen),
            "last_lat": vessel.last_lat,
            "last_lon": vessel.last_lon,
            "last_speed": vessel.last_speed,
            "last_course": vessel.last_course,
            "last_source": vessel.last_source,
            "operational_state": vessel.operational_state,
            "draft": vessel.draft,
            "dwt": vessel.dwt,
        },
        "positions_summary": {
            "raw_count": len(pos_rows),
            "clean_count": len(clean_rows),
            "rejected_count": len(rejected_rows),
            "rejected_sample": [
                {
                    "time": _serialize_value(r.time),
                    "lat": r.latitude,
                    "lon": r.longitude,
                    "source": r.source,
                }
                for r in rejected_rows[:50]
            ],
        },
        "positions": [
            {
                "time": _serialize_value(r.time),
                "lat": r.latitude,
                "lon": r.longitude,
                "speed": r.speed,
                "course": r.course,
                "source": r.source,
            }
            for r in recent_raw
        ],
        "chokepoint_transits": [
            {
                "id": str(t.id),
                "chokepoint_id": t.chokepoint_id,
                "entry_time": _serialize_value(t.entry_time),
                "exit_time": _serialize_value(t.exit_time),
                "direction": t.direction,
                "duration_min": t.transit_duration_min,
                "method": t.detection_method,
            }
            for t in transits
        ],
        "state_history": [
            {
                "state": s.state,
                "cargo_status": s.cargo_status,
                "port_id": s.port_id,
                "started_at": _serialize_value(s.started_at),
                "ended_at": _serialize_value(s.ended_at),
                "duration_hours": s.duration_hours,
            }
            for s in states
        ],
        "ais_gaps": [
            {
                "id": str(g.id),
                "gap_start": _serialize_value(g.gap_start),
                "gap_end": _serialize_value(g.gap_end),
                "status": g.status,
                "distance_jumped_km": g.distance_jumped_km,
                "spoofing_suspected": g.spoofing_suspected,
            }
            for g in gaps
        ],
    }


@router.get("/db-stats", dependencies=[Depends(require_debug_token)])
async def debug_db_stats(db: AsyncSession = Depends(get_db)):
    """High-level database health: table estimates, 24h AIS source breakdown, TimescaleDB chunks, recent errors."""
    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=24)
    cutoff_1h = now - timedelta(hours=1)

    table_rows = (
        await db.execute(
            text(
                "SELECT relname, n_live_tup "
                "FROM pg_stat_user_tables "
                "ORDER BY n_live_tup DESC"
            )
        )
    ).all()
    tables = {r[0]: int(r[1] or 0) for r in table_rows}

    source_rows = (
        await db.execute(
            select(
                VesselPosition.source,
                func.count().label("positions_24h"),
                func.max(VesselPosition.time).label("latest_time"),
            )
            .where(VesselPosition.time >= cutoff_24h)
            .group_by(VesselPosition.source)
            .order_by(desc("positions_24h"))
        )
    ).all()

    sources_24h = [
        {
            "source": r.source or "unknown",
            "positions_24h": int(r.positions_24h),
            "latest_time": _serialize_value(r.latest_time),
        }
        for r in source_rows
    ]

    error_rows = (
        await db.execute(
            select(
                SystemLog.service,
                SystemLog.level,
                func.count().label("count_24h"),
                func.count().filter(SystemLog.created_at >= cutoff_1h).label("count_1h"),
            )
            .where(
                SystemLog.created_at >= cutoff_24h,
                SystemLog.level.in_(("ERROR", "CRITICAL", "WARNING")),
            )
            .group_by(SystemLog.service, SystemLog.level)
            .order_by(SystemLog.service, SystemLog.level)
        )
    ).all()
    log_summary = [
        {
            "service": r.service,
            "level": r.level,
            "count_1h": int(r.count_1h),
            "count_24h": int(r.count_24h),
        }
        for r in error_rows
    ]

    chunks: list[dict[str, Any]] = []
    try:
        async with db.begin_nested():
            chunk_rows = (
                await db.execute(
                    text(
                        "SELECT hypertable_name, chunk_name, is_compressed, range_start, range_end "
                        "FROM timescaledb_information.chunks "
                        "ORDER BY hypertable_name, range_start DESC "
                        "LIMIT 50"
                    )
                )
            ).all()
            chunks = [
                {
                    "hypertable": r[0],
                    "chunk": r[1],
                    "is_compressed": bool(r[2]),
                    "range_start": _serialize_value(r[3]),
                    "range_end": _serialize_value(r[4]),
                }
                for r in chunk_rows
            ]
    except Exception:
        chunks = []

    return {
        "generated_at": now.isoformat(),
        "tables_estimated_rows": tables,
        "ais_sources_24h": sources_24h,
        "log_summary_24h": log_summary,
        "timescaledb_chunks": chunks,
    }


@router.get("/logs", dependencies=[Depends(require_debug_token)])
async def debug_logs(
    service: Optional[str] = Query(None),
    level: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    minutes: int = Query(60, ge=1, le=10080),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Tail centralized system_logs entries."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    query = select(SystemLog).where(SystemLog.created_at >= cutoff)

    if service:
        query = query.where(SystemLog.service == service)
    if level:
        query = query.where(SystemLog.level == level.upper())
    if search:
        query = query.where(SystemLog.message.ilike(f"%{search}%"))

    query = query.order_by(desc(SystemLog.created_at)).limit(limit)
    rows = list((await db.execute(query)).scalars().all())

    return {
        "count": len(rows),
        "logs": [
            {
                "id": r.id,
                "created_at": _serialize_value(r.created_at),
                "service": r.service,
                "level": r.level,
                "logger": r.logger_name,
                "message": r.message,
            }
            for r in rows
        ],
    }


@router.post("/query", dependencies=[Depends(require_debug_token)])
async def debug_readonly_query(
    req: DebugQueryRequest,
    db: AsyncSession = Depends(get_db),
):
    """Execute a strictly read-only SQL query (SELECT/WITH/EXPLAIN) with 5s timeout and row cap."""
    cleaned_sql = validate_readonly_sql(req.sql)

    try:
        async with db.begin_nested():
            await db.execute(text("SET LOCAL TRANSACTION READ ONLY"))
            await db.execute(text("SET LOCAL statement_timeout = '5000'"))
            result = await db.execute(text(cleaned_sql))
            columns = list(result.keys())
            raw_rows = result.fetchmany(req.limit + 1)
    except DBAPIError as exc:
        raise HTTPException(status_code=400, detail=f"Query failed: {exc.orig or exc}")

    truncated = len(raw_rows) > req.limit
    rows_to_return = raw_rows[: req.limit]

    return {
        "columns": columns,
        "row_count": len(rows_to_return),
        "truncated": truncated,
        "rows": [
            {col: _serialize_value(val) for col, val in zip(columns, row)}
            for row in rows_to_return
        ],
    }
