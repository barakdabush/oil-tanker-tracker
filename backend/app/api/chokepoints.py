"""Chokepoint API endpoints."""

from datetime import datetime, timezone, timedelta
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy import select, func, text, and_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Chokepoint, ChokepointTransit
from app.schemas import ChokepointResponse

router = APIRouter()

@router.get("", response_model=List[ChokepointResponse])
async def list_chokepoints(
    hours: int = 0,
    db: AsyncSession = Depends(get_db)
):
    """List all chokepoints and calculate their dynamic congestion status based on recent transits."""
    
    # Get all chokepoints with their GeoJSON boundaries and gate lines
    chokepoints_stmt = text(
        "SELECT id, name, region, avg_daily_oil_flow_mbd, congestion_threshold, "
        "ST_AsGeoJSON(boundary_polygon) as geojson, ST_AsGeoJSON(gate_line) as gate_geojson "
        "FROM chokepoints"
    )
    chokepoints_result = await db.execute(chokepoints_stmt)
    chokepoints = chokepoints_result.all()
    
    if hours == 0:
        transit_counts_stmt = (
            select(
                ChokepointTransit.chokepoint_id,
                func.count().label("transit_count")
            )
            .group_by(ChokepointTransit.chokepoint_id)
        )
    else:
        cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
        transit_counts_stmt = (
            select(
                ChokepointTransit.chokepoint_id,
                func.count().label("transit_count")
            )
            .where(ChokepointTransit.entry_time >= cutoff_time)
            .group_by(ChokepointTransit.chokepoint_id)
        )
    
    transit_counts_result = await db.execute(transit_counts_stmt)
    transit_counts = {row.chokepoint_id: row.transit_count for row in transit_counts_result}
    
    response = []
    import json
    
    for cp in chokepoints:
        count = transit_counts.get(cp.id, 0)
        
        # Calculate dynamic status
        if hours == 0:
            status = "normal" # Can't meaningfully calculate congestion for 'all time' without full history span
        else:
            threshold_24h = cp.congestion_threshold or 20
            scaled_threshold = threshold_24h * (hours / 24.0)
            
            if count > scaled_threshold * 1.5:
                status = "congested"
            elif count < scaled_threshold * 0.5:
                status = "low"
            else:
                status = "normal"
            
        response.append(
            ChokepointResponse(
                id=cp.id,
                name=cp.name,
                region=cp.region,
                avg_daily_oil_flow_mbd=cp.avg_daily_oil_flow_mbd,
                congestion_threshold=cp.congestion_threshold,
                current_vessel_count=count,
                congestion_status=status,
                boundary_geojson=json.loads(cp.geojson) if cp.geojson else None,
                gate_line_geojson=json.loads(cp.gate_geojson) if cp.gate_geojson else None,
            )
        )
        
    return response

@router.get("/portwatch")
async def get_portwatch_chokepoint(
    portname: str = "Strait of Hormuz",
    days: int = 90,
    db: AsyncSession = Depends(get_db),
):
    """Get IMF PortWatch daily satellite-AIS transit history and baseline comparison."""
    from app.models import PortWatchChokepointDaily

    days = max(7, min(days, 365))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date()
    baseline_cutoff = (datetime.now(timezone.utc) - timedelta(days=365)).date()

    # Recent series for requested chokepoint (case-insensitive substring match for convenience)
    series_q = (
        select(PortWatchChokepointDaily)
        .where(
            PortWatchChokepointDaily.portname.ilike(f"%{portname}%"),
            PortWatchChokepointDaily.date >= cutoff,
        )
        .order_by(PortWatchChokepointDaily.date.asc())
    )
    rows = (await db.execute(series_q)).scalars().all()

    if not rows:
        return {
            "portname": portname,
            "portid": None,
            "latest_date": None,
            "latest_total": 0,
            "latest_tanker": 0,
            "seven_day_avg_total": 0.0,
            "seven_day_avg_tanker": 0.0,
            "baseline_avg_total": 0.0,
            "throughput_pct": None,
            "series": [],
        }

    portid = rows[-1].portid
    resolved_name = rows[-1].portname

    # Baseline: 365-day average (or full stored window up to 365d)
    baseline_q = select(
        func.avg(PortWatchChokepointDaily.n_total),
        func.avg(PortWatchChokepointDaily.n_tanker),
    ).where(
        PortWatchChokepointDaily.portid == portid,
        PortWatchChokepointDaily.date >= baseline_cutoff,
    )
    base_row = (await db.execute(baseline_q)).first()
    baseline_total = float(base_row[0] or 0.0) if base_row else 0.0
    baseline_tanker = float(base_row[1] or 0.0) if base_row else 0.0

    last_7 = rows[-7:]
    avg_7_total = round(sum(r.n_total for r in last_7) / len(last_7), 1)
    avg_7_tanker = round(sum(r.n_tanker for r in last_7) / len(last_7), 1)
    latest = rows[-1]
    throughput_pct = round((latest.n_total / baseline_total) * 100.0, 1) if baseline_total > 0 else None

    return {
        "portname": resolved_name,
        "portid": portid,
        "latest_date": latest.date.isoformat(),
        "latest_total": latest.n_total,
        "latest_tanker": latest.n_tanker,
        "seven_day_avg_total": avg_7_total,
        "seven_day_avg_tanker": avg_7_tanker,
        "baseline_avg_total": round(baseline_total, 1),
        "baseline_avg_tanker": round(baseline_tanker, 1),
        "throughput_pct": throughput_pct,
        "series": [
            {
                "date": r.date.isoformat(),
                "n_total": r.n_total,
                "n_tanker": r.n_tanker,
                "n_cargo": r.n_cargo,
                "n_container": r.n_container,
                "capacity_tanker": r.capacity_tanker,
            }
            for r in rows
        ],
    }


@router.get("/portwatch/summary")
async def get_portwatch_summary(db: AsyncSession = Depends(get_db)):
    """Get latest IMF PortWatch transit snapshot across major global chokepoints."""
    query = text("""
        WITH latest AS (
            SELECT DISTINCT ON (portid)
                portid, portname, date, n_total, n_tanker, capacity_tanker
            FROM portwatch_chokepoint_daily
            ORDER BY portid, date DESC
        ),
        avg7 AS (
            SELECT portid,
                   ROUND(AVG(n_total)::numeric, 1) AS avg_7d_total,
                   ROUND(AVG(n_tanker)::numeric, 1) AS avg_7d_tanker
            FROM (
                SELECT portid, n_total, n_tanker,
                       ROW_NUMBER() OVER (PARTITION BY portid ORDER BY date DESC) AS rn
                FROM portwatch_chokepoint_daily
            ) sub
            WHERE rn <= 7
            GROUP BY portid
        )
        SELECT l.portid, l.portname, l.date, l.n_total, l.n_tanker, l.capacity_tanker,
               COALESCE(a.avg_7d_total, l.n_total) AS avg_7d_total,
               COALESCE(a.avg_7d_tanker, l.n_tanker) AS avg_7d_tanker
        FROM latest l
        LEFT JOIN avg7 a ON l.portid = a.portid
        ORDER BY l.n_tanker DESC
    """)
    rows = (await db.execute(query)).all()
    return [
        {
            "portid": r.portid,
            "portname": r.portname,
            "latest_date": r.date.isoformat(),
            "latest_total": r.n_total,
            "latest_tanker": r.n_tanker,
            "capacity_tanker": r.capacity_tanker,
            "avg_7d_total": float(r.avg_7d_total),
            "avg_7d_tanker": float(r.avg_7d_tanker),
        }
        for r in rows
    ]


@router.post("/recalculate")
async def recalculate_chokepoint_transits(
    days: Optional[int] = Query(
        30,
        ge=1,
        le=730,
        description="Replay vessel_positions from the last N days (omit with all_history=true for full history)",
    ),
    all_history: bool = Query(
        False,
        description="If true, replay all available vessel_positions history",
    ),
    replace_existing: bool = Query(
        False,
        description="If true, delete existing chokepoint_transits in the window before rebuilding; if false, only insert missed transits (idempotent)",
    ),
    dry_run: bool = Query(
        False,
        description="If true, return counts without modifying the database",
    ),
    recalculate_snapshots: bool = Query(
        False,
        description="If true (and not dry_run), also recalculate daily oil_market_snapshots for the backfilled window",
    ),
    db: AsyncSession = Depends(get_db),
):
    """Recalculate historical chokepoint transits using the hybrid polygon + gate-line algorithm."""
    from app.services.chokepoint_monitor import ChokepointMonitor

    monitor = ChokepointMonitor()
    effective_days = None if all_history else days
    stats = await monitor.backfill_transits(
        days=effective_days,
        replace_existing=replace_existing,
        dry_run=dry_run,
    )

    snapshots_recalculated = 0
    if recalculate_snapshots and not dry_run:
        from app.models import OilMarketSnapshot
        from app.services.global_oil_feature_builder import GlobalOilFeatureBuilder

        now_utc = datetime.now(timezone.utc)
        today = now_utc.date()
        yesterday = today - timedelta(days=1)
        if effective_days is not None:
            cutoff = (now_utc - timedelta(days=effective_days)).date()
            existing_dates = (
                await db.execute(
                    select(OilMarketSnapshot.snapshot_date)
                    .where(OilMarketSnapshot.snapshot_date >= cutoff)
                    .order_by(OilMarketSnapshot.snapshot_date.asc())
                )
            ).scalars().all()
        else:
            existing_dates = (
                await db.execute(
                    select(OilMarketSnapshot.snapshot_date)
                    .order_by(OilMarketSnapshot.snapshot_date.asc())
                )
            ).scalars().all()

        dates_to_recalc = sorted(set(existing_dates) | {yesterday, today})
        builder = GlobalOilFeatureBuilder()
        for target_date in dates_to_recalc:
            await builder.aggregate(target_date=target_date)
        snapshots_recalculated = len(dates_to_recalc)

    return {
        **stats,
        "snapshots_recalculated": snapshots_recalculated,
    }


@router.get("/{chokepoint_id}/outline")
async def get_chokepoint_outline(chokepoint_id: int, db: AsyncSession = Depends(get_db)):
    """Return the GeoJSON polygon for a specific chokepoint to draw on the map."""
    stmt = text("SELECT ST_AsGeoJSON(boundary_polygon) as geojson FROM chokepoints WHERE id = :id")
    result = await db.execute(stmt, {"id": chokepoint_id})
    row = result.first()
    if row and row.geojson:
        import json
        return json.loads(row.geojson)
    from fastapi import HTTPException
    raise HTTPException(status_code=404, detail="Chokepoint outline not found")
