"""Analytics API endpoints — supply chain analysis and fleet statistics."""

from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, and_, desc, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import (
    Vessel, CargoEvent, VoyageLeg, Port,
    AISGap, STSEvent, ChokepointTransit,
)
from app.schemas import (
    FlowDataPoint, VolumeOverTime, TopRoute,
    FleetStatus, DashboardStats, CargoEventResponse,
    IngestionDataPoint,
)

router = APIRouter()


@router.get("/dashboard", response_model=DashboardStats)
async def get_dashboard(db: AsyncSession = Depends(get_db)):
    """Get dashboard summary stats."""
    now = datetime.now(timezone.utc)

    # Fleet counts
    total = (await db.execute(select(func.count()).select_from(Vessel))).scalar() or 0
    in_transit = (await db.execute(
        select(func.count()).select_from(Vessel).where(Vessel.last_speed > 3)
    )).scalar() or 0
    at_port = (await db.execute(
        select(func.count()).select_from(Vessel).where(Vessel.last_speed <= 1)
    )).scalar() or 0

    # Dark vessels
    cutoff_dark = now - timedelta(hours=6)
    currently_dark = (await db.execute(
        select(func.count()).select_from(AISGap).where(
            and_(AISGap.status.in_(["dark", "extended_dark"]), AISGap.gap_end.is_(None))
        )
    )).scalar() or 0

    # Ongoing STS events
    ongoing_sts = (await db.execute(
        select(func.count()).select_from(STSEvent).where(
            STSEvent.status.in_(["detected", "ongoing"])
        )
    )).scalar() or 0

    # Recent cargo events (last 24h)
    cutoff_24h = now - timedelta(hours=24)
    cargo_q = (
        select(CargoEvent, Port.name.label("port_name"))
        .outerjoin(Port, CargoEvent.port_id == Port.id)
        .where(CargoEvent.created_at > cutoff_24h)
        .order_by(desc(CargoEvent.created_at))
        .limit(10)
    )
    cargo_result = await db.execute(cargo_q)
    recent_cargo_rows = cargo_result.all()
    
    cargo_events_formatted = []
    for row in recent_cargo_rows:
        evt = row.CargoEvent
        # Add the label result as an attribute so model_validate can pick it up
        evt.port_name = row.port_name
        cargo_events_formatted.append(CargoEventResponse.model_validate(evt))

    # Daily volume
    volume_result = await db.execute(
        select(func.coalesce(func.sum(CargoEvent.estimated_volume_barrels), 0))
        .where(CargoEvent.created_at > cutoff_24h)
    )
    daily_volume = volume_result.scalar() or 0

    # Active alerts = dark + ongoing sts
    active_alerts = currently_dark + ongoing_sts

    return DashboardStats(
        fleet=FleetStatus(
            total_vessels=total,
            in_transit=in_transit,
            at_port=at_port,
            currently_dark=currently_dark,
            loaded=0,  # Would need draft analysis
            ballast=0,
        ),
        active_alerts=active_alerts,
        ongoing_sts_events=ongoing_sts,
        congested_chokepoints=0,
        recent_cargo_events=cargo_events_formatted,
        daily_volume_barrels=daily_volume,
    )


@router.get("/flow", response_model=list[FlowDataPoint])
async def get_oil_flow(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """Get oil flow between regions (aggregated cargo events)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Join cargo events with ports to get regions
    query = (
        select(
            Port.region.label("source_region"),
            func.coalesce(func.sum(CargoEvent.estimated_volume_barrels), 0).label("volume"),
            func.count().label("count"),
        )
        .join(Port, CargoEvent.port_id == Port.id)
        .where(and_(
            CargoEvent.created_at > cutoff,
            CargoEvent.event_type == "loading",
        ))
        .group_by(Port.region)
        .order_by(desc("volume"))
    )
    result = await db.execute(query)
    rows = result.all()

    return [
        FlowDataPoint(
            source_region=row.source_region or "Unknown",
            destination_region="Global",
            volume_barrels=float(row.volume),
            vessel_count=row.count,
        )
        for row in rows
    ]


@router.get("/volume", response_model=list[VolumeOverTime])
async def get_volume_over_time(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """Get estimated oil volume moved over time."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    query = (
        select(
            func.date_trunc("day", CargoEvent.created_at).label("date"),
            func.coalesce(func.sum(CargoEvent.estimated_volume_barrels), 0).label("volume"),
            func.count().label("count"),
        )
        .where(CargoEvent.created_at > cutoff)
        .group_by("date")
        .order_by("date")
    )
    result = await db.execute(query)
    rows = result.all()

    return [
        VolumeOverTime(
            date=str(row.date.date()) if row.date else "",
            volume_barrels=float(row.volume),
            event_count=row.count,
        )
        for row in rows
    ]


@router.get("/top-routes", response_model=list[TopRoute])
async def get_top_routes(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Get most trafficked tanker routes."""
    from sqlalchemy.orm import aliased
    origin = aliased(Port)
    dest = aliased(Port)

    query = (
        select(
            origin.name.label("origin_port"),
            dest.name.label("dest_port"),
            func.count().label("voyage_count"),
            func.coalesce(func.sum(VoyageLeg.estimated_volume_barrels), 0).label("total_volume"),
        )
        .join(origin, VoyageLeg.origin_port_id == origin.id)
        .join(dest, VoyageLeg.destination_port_id == dest.id)
        .group_by(origin.name, dest.name)
        .order_by(desc("voyage_count"))
        .limit(limit)
    )
    result = await db.execute(query)
    rows = result.all()

    return [
        TopRoute(
            origin_port=row.origin_port,
            destination_port=row.dest_port,
            voyage_count=row.voyage_count,
            total_volume_barrels=float(row.total_volume),
        )
        for row in rows
    ]


@router.get("/fleet-status", response_model=FleetStatus)
async def get_fleet_status(db: AsyncSession = Depends(get_db)):
    """Get current fleet status breakdown."""
    total = (await db.execute(select(func.count()).select_from(Vessel))).scalar() or 0
    in_transit = (await db.execute(
        select(func.count()).select_from(Vessel).where(Vessel.last_speed > 3)
    )).scalar() or 0
    at_port = (await db.execute(
        select(func.count()).select_from(Vessel).where(Vessel.last_speed <= 1)
    )).scalar() or 0
    currently_dark = (await db.execute(
        select(func.count()).select_from(AISGap).where(
            and_(AISGap.status.in_(["dark", "extended_dark"]), AISGap.gap_end.is_(None))
        )
    )).scalar() or 0

    return FleetStatus(
        total_vessels=total,
        in_transit=in_transit,
        at_port=at_port,
        currently_dark=currently_dark,
        loaded=0,
        ballast=0,
    )


@router.get("/ingestion", response_model=list[IngestionDataPoint])
async def get_ingestion_analytics(
    hours: int = Query(24, ge=1, le=168),
    db: AsyncSession = Depends(get_db),
):
    """Get data ingestion analytics — row counts per time bucket across all tables.

    Useful for monitoring WebSocket health and data pipeline throughput.
    Returns counts of new rows added to vessel_positions, chokepoint_transits,
    sts_events, ais_gaps, and cargo_events over time.
    """
    # Dynamic bucket size based on time range
    if hours <= 6:
        bucket = "1 minute"
    elif hours <= 24:
        bucket = "5 minutes"
    else:
        bucket = "30 minutes"

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    # Query hypertables with time_bucket, regular tables with date_trunc
    queries = {
        "vessel_positions": text(f"""
            SELECT time_bucket('{bucket}', time) AS bucket, COUNT(*) AS cnt
            FROM vessel_positions
            WHERE time > :cutoff
            GROUP BY bucket ORDER BY bucket
        """),
        "chokepoint_transits": text(f"""
            SELECT time_bucket('{bucket}', time) AS bucket, COUNT(*) AS cnt
            FROM chokepoint_transits
            WHERE time > :cutoff
            GROUP BY bucket ORDER BY bucket
        """),
        "sts_events": text(f"""
            SELECT date_trunc('minute', created_at) -
                   (EXTRACT(minute FROM created_at)::int
                    % {_bucket_minutes(bucket)}) * INTERVAL '1 minute' AS bucket,
                   COUNT(*) AS cnt
            FROM sts_events
            WHERE created_at > :cutoff
            GROUP BY bucket ORDER BY bucket
        """),
        "ais_gaps": text(f"""
            SELECT date_trunc('minute', created_at) -
                   (EXTRACT(minute FROM created_at)::int
                    % {_bucket_minutes(bucket)}) * INTERVAL '1 minute' AS bucket,
                   COUNT(*) AS cnt
            FROM ais_gaps
            WHERE created_at > :cutoff
            GROUP BY bucket ORDER BY bucket
        """),
        "cargo_events": text(f"""
            SELECT date_trunc('minute', created_at) -
                   (EXTRACT(minute FROM created_at)::int
                    % {_bucket_minutes(bucket)}) * INTERVAL '1 minute' AS bucket,
                   COUNT(*) AS cnt
            FROM cargo_events
            WHERE created_at > :cutoff
            GROUP BY bucket ORDER BY bucket
        """),
    }

    # Collect all results into a merged dict keyed by timestamp
    merged: dict[str, dict[str, int]] = {}

    for table_name, query in queries.items():
        try:
            result = await db.execute(query, {"cutoff": cutoff})
            for row in result.all():
                ts = row.bucket.isoformat() if row.bucket else ""
                if ts not in merged:
                    merged[ts] = {}
                merged[ts][table_name] = row.cnt
        except Exception:
            # Table might not exist yet or query might fail — skip gracefully
            continue

    # Build sorted response
    sorted_timestamps = sorted(merged.keys())
    return [
        IngestionDataPoint(
            timestamp=ts,
            vessel_positions=merged[ts].get("vessel_positions", 0),
            chokepoint_transits=merged[ts].get("chokepoint_transits", 0),
            sts_events=merged[ts].get("sts_events", 0),
            ais_gaps=merged[ts].get("ais_gaps", 0),
            cargo_events=merged[ts].get("cargo_events", 0),
        )
        for ts in sorted_timestamps
    ]


def _bucket_minutes(bucket: str) -> int:
    """Convert bucket string to minutes for date_trunc math."""
    if "1 minute" in bucket and "30" not in bucket:
        return 1
    if "5 minute" in bucket:
        return 5
    if "30 minute" in bucket:
        return 30
    return 5

