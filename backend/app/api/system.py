"""System health, data pipeline freshness, and centralized container logs API."""

from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.system_log import SystemLog

router = APIRouter()


async def _fetch_websocket_health() -> dict:
    """Query the websocket service /api/health over internal Docker network or localhost."""
    urls = ["http://websocket:8001/api/health", "http://localhost:8001/api/health"]
    async with httpx.AsyncClient(timeout=2.5) as client:
        for url in urls:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return {"reachable": True, "data": resp.json()}
            except Exception:
                continue
    return {"reachable": False, "data": None}


async def _fetch_backend_health() -> bool:
    """Probe the backend API /api/health endpoint when called from an external monitor."""
    urls = ["http://backend:8000/api/health", "http://localhost:8000/api/health"]
    async with httpx.AsyncClient(timeout=2.5) as client:
        for url in urls:
            try:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return True
            except Exception:
                continue
    return False



def _freshness_status(latest: Optional[datetime], stale_after_hours: float) -> str:
    if latest is None:
        return "empty"
    if latest.tzinfo is None:
        latest = latest.replace(tzinfo=timezone.utc)
    age_hours = (datetime.now(timezone.utc) - latest).total_seconds() / 3600.0
    return "healthy" if age_hours <= stale_after_hours else "stale"


@router.get("/health")
async def get_system_health(db: AsyncSession = Depends(get_db)):
    """Return container health, AIS source stats, and data pipeline freshness."""
    return await _build_system_health(db, check_backend=False)


async def _build_system_health(db: AsyncSession, check_backend: bool = False) -> dict:
    """Compute container health, AIS source stats, and data pipeline freshness."""
    now = datetime.now(timezone.utc)

    # 1. Database check
    db_ok = False
    try:
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    # 2. WebSocket / AIS ingestion check
    ws_info = await _fetch_websocket_health()
    ws_data = ws_info.get("data") or {}
    sources = ws_data.get("sources") or {}

    # 3. Pipeline freshness queries
    pipelines = []
    if db_ok:
        # Vessel positions (24h count + latest timestamp)
        vp_row = (await db.execute(text("""
            SELECT MAX(time) AS latest_time,
                   COUNT(*) FILTER (WHERE time > NOW() - INTERVAL '24 hours') AS count_24h
            FROM vessel_positions
        """))).first()
        vp_latest = vp_row.latest_time if vp_row else None
        pipelines.append({
            "name": "AIS Vessel Positions",
            "source": "aisstream.io + openwaters.io",
            "latest_at": vp_latest.isoformat() if vp_latest else None,
            "count": int(vp_row.count_24h or 0) if vp_row else 0,
            "count_label": "last 24h",
            "status": _freshness_status(vp_latest, 1.0),
        })

        # Tracked vessels
        v_row = (await db.execute(text("""
            SELECT MAX(last_seen) AS latest_time, COUNT(*) AS cnt FROM vessels
        """))).first()
        v_latest = v_row.latest_time if v_row else None
        pipelines.append({
            "name": "Tracked Tankers",
            "source": "vessels table",
            "latest_at": v_latest.isoformat() if v_latest else None,
            "count": int(v_row.cnt or 0) if v_row else 0,
            "count_label": "total",
            "status": _freshness_status(v_latest, 2.0),
        })

        # Oil prices (Yahoo Finance + EIA)
        op_row = (await db.execute(text("""
            SELECT MAX(price_date) AS latest_date, MAX(fetched_at) AS latest_fetch, COUNT(*) AS cnt
            FROM oil_prices
        """))).first()
        op_latest_dt = (
            datetime.combine(op_row.latest_date, datetime.min.time(), tzinfo=timezone.utc)
            if op_row and op_row.latest_date else None
        )
        pipelines.append({
            "name": "Oil Prices (Brent / WTI)",
            "source": "Yahoo Finance / EIA",
            "latest_at": op_row.latest_date.isoformat() if op_row and op_row.latest_date else None,
            "fetched_at": op_row.latest_fetch.isoformat() if op_row and op_row.latest_fetch else None,
            "count": int(op_row.cnt or 0) if op_row else 0,
            "count_label": "days",
            "status": _freshness_status(op_latest_dt, 84.0),  # allow weekend gap (~3.5d)
        })

        # IMF PortWatch
        pw_row = (await db.execute(text("""
            SELECT MAX(date) AS latest_date, MAX(updated_at) AS latest_fetch, COUNT(*) AS cnt
            FROM portwatch_chokepoint_daily
        """))).first()
        pw_fetch = pw_row.latest_fetch if pw_row else None
        pipelines.append({
            "name": "IMF PortWatch Transits",
            "source": "IMF ArcGIS (weekly ~7-9d lag)",
            "latest_at": pw_row.latest_date.isoformat() if pw_row and pw_row.latest_date else None,
            "fetched_at": pw_fetch.isoformat() if pw_fetch else None,
            "count": int(pw_row.cnt or 0) if pw_row else 0,
            "count_label": "rows",
            "status": _freshness_status(pw_fetch, 24.0),
        })

        # OFAC SDN Sanctions
        ofac_row = (await db.execute(text("""
            SELECT MAX(updated_at) AS latest_fetch, COUNT(*) AS cnt FROM ofac_sanctioned_vessels
        """))).first()
        ofac_fetch = ofac_row.latest_fetch if ofac_row else None
        pipelines.append({
            "name": "OFAC SDN Sanctions",
            "source": "US Treasury SDN.CSV",
            "latest_at": ofac_fetch.isoformat() if ofac_fetch else None,
            "count": int(ofac_row.cnt or 0) if ofac_row else 0,
            "count_label": "vessels",
            "status": _freshness_status(ofac_fetch, 24.0),
        })

        # Dark fleet / STS / Market snapshots
        gap_row = (await db.execute(text("SELECT MAX(created_at) AS latest_at, COUNT(*) AS cnt FROM ais_gaps"))).first()
        pipelines.append({
            "name": "Dark Fleet (AIS Gaps)",
            "source": "dark_fleet_detector",
            "latest_at": gap_row.latest_at.isoformat() if gap_row and gap_row.latest_at else None,
            "count": int(gap_row.cnt or 0) if gap_row else 0,
            "count_label": "events",
            "status": "healthy" if gap_row and gap_row.cnt else "empty",
        })

        sts_row = (await db.execute(text("SELECT MAX(created_at) AS latest_at, COUNT(*) AS cnt FROM sts_events"))).first()
        pipelines.append({
            "name": "STS Transfers",
            "source": "sts_detector",
            "latest_at": sts_row.latest_at.isoformat() if sts_row and sts_row.latest_at else None,
            "count": int(sts_row.cnt or 0) if sts_row else 0,
            "count_label": "events",
            "status": "healthy" if sts_row and sts_row.cnt else "empty",
        })

        snap_row = (await db.execute(text("SELECT MAX(snapshot_date) AS latest_date, COUNT(*) AS cnt FROM oil_market_snapshots"))).first()
        pipelines.append({
            "name": "Oil Market Snapshots",
            "source": "global_oil_feature_builder",
            "latest_at": snap_row.latest_date.isoformat() if snap_row and snap_row.latest_date else None,
            "count": int(snap_row.cnt or 0) if snap_row else 0,
            "count_label": "days",
            "status": _freshness_status(
                datetime.combine(snap_row.latest_date, datetime.min.time(), tzinfo=timezone.utc)
                if snap_row and snap_row.latest_date else None,
                54.0,  # snapshot_date is yesterday UTC; 24h day + 24h cycle + 6h grace
            ),
        })

        # 4. Recent log summary per service (last 1h)
        cutoff_1h = now - timedelta(hours=1)
        log_stats_rows = (await db.execute(text("""
            SELECT service,
                   MAX(created_at) AS last_log_at,
                   COUNT(*) FILTER (WHERE level IN ('ERROR', 'CRITICAL') AND created_at >= :cutoff) AS errors_1h,
                   COUNT(*) FILTER (WHERE level = 'WARNING' AND created_at >= :cutoff) AS warnings_1h
            FROM system_logs
            GROUP BY service
        """), {"cutoff": cutoff_1h})).all()
        log_stats = {
            r.service: {
                "last_log_at": r.last_log_at.isoformat() if r.last_log_at else None,
                "errors_1h": int(r.errors_1h or 0),
                "warnings_1h": int(r.warnings_1h or 0),
            }
            for r in log_stats_rows
        }
    else:
        log_stats = {}

    scheduler_stats = log_stats.get("scheduler", {"last_log_at": None, "errors_1h": 0, "warnings_1h": 0})
    ws_stats = log_stats.get("websocket", {"last_log_at": None, "errors_1h": 0, "warnings_1h": 0})
    backend_stats = log_stats.get("backend", {"last_log_at": None, "errors_1h": 0, "warnings_1h": 0})

    backend_reachable = (await _fetch_backend_health()) if check_backend else True
    if not db_ok or not backend_reachable:
        backend_status = "down"
    elif backend_stats["errors_1h"] > 0:
        backend_status = "degraded"
    else:
        backend_status = "healthy"

    scheduler_last_dt = None
    if scheduler_stats["last_log_at"]:
        scheduler_last_dt = datetime.fromisoformat(scheduler_stats["last_log_at"])
        if scheduler_last_dt.tzinfo is None:
            scheduler_last_dt = scheduler_last_dt.replace(tzinfo=timezone.utc)
    scheduler_stale = scheduler_last_dt is None or (now - scheduler_last_dt) > timedelta(minutes=20)
    if scheduler_stale:
        scheduler_status = "down"
    elif scheduler_stats["errors_1h"] > 0:
        scheduler_status = "degraded"
    else:
        scheduler_status = "healthy"

    services = [
        {
            "name": "backend",
            "label": "Backend API",
            "status": backend_status,
            "errors_1h": backend_stats["errors_1h"],
            "warnings_1h": backend_stats["warnings_1h"],
            "last_log_at": backend_stats["last_log_at"],
        },
        {
            "name": "websocket",
            "label": "WebSocket / AIS",
            "status": "healthy" if ws_info["reachable"] and ws_stats["errors_1h"] == 0 else ("degraded" if ws_info["reachable"] else "down"),
            "errors_1h": ws_stats["errors_1h"],
            "warnings_1h": ws_stats["warnings_1h"],
            "last_log_at": ws_stats["last_log_at"],
            "ais_sources": sources,
        },
        {
            "name": "scheduler",
            "label": "Scheduler Worker",
            "status": scheduler_status,
            "errors_1h": scheduler_stats["errors_1h"],
            "warnings_1h": scheduler_stats["warnings_1h"],
            "last_log_at": scheduler_stats["last_log_at"],
        },
        {
            "name": "db",
            "label": "TimescaleDB",
            "status": "healthy" if db_ok else "down",
            "errors_1h": 0,
            "warnings_1h": 0,
            "last_log_at": now.isoformat(),
        },
    ]

    return {
        "checked_at": now.isoformat(),
        "services": services,
        "pipelines": pipelines,
    }


@router.get("/logs")
async def get_system_logs(
    service: Optional[str] = Query(None, description="Filter by service: backend, websocket, scheduler"),
    level: Optional[str] = Query(None, description="Filter by level: INFO, WARNING, ERROR"),
    search: Optional[str] = Query(None, description="Search substring in message or logger"),
    limit: int = Query(150, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Return recent application & worker logs stored in system_logs."""
    query = select(SystemLog)

    if service and service.lower() != "all":
        query = query.where(SystemLog.service == service.lower())
    if level and level.upper() != "ALL":
        query = query.where(SystemLog.level == level.upper())
    if search:
        pattern = f"%{search}%"
        query = query.where(
            SystemLog.message.ilike(pattern) | SystemLog.logger_name.ilike(pattern)
        )

    query = query.order_by(desc(SystemLog.id)).limit(limit)
    rows = (await db.execute(query)).scalars().all()

    return {
        "total_returned": len(rows),
        "logs": [
            {
                "id": r.id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "service": r.service,
                "level": r.level,
                "logger_name": r.logger_name,
                "message": r.message,
            }
            for r in rows
        ],
    }
