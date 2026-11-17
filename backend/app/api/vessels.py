"""Vessel API endpoints."""

from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, and_, desc, or_, String
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Vessel, VesselPosition, CargoEvent, OfacSanctionedVessel
from app.schemas import VesselResponse, VesselListResponse, PositionResponse, TrailResponse, CargoEventResponse

router = APIRouter()


async def _get_sanction_maps(db: AsyncSession, vessels: list[Vessel]) -> tuple[dict[int, str], dict[int, str]]:
    """Return ({imo: program}, {mmsi: program}) maps for a batch of vessels."""
    imos = [v.imo for v in vessels if v.imo]
    mmsis = [v.mmsi for v in vessels if v.mmsi]
    if not imos and not mmsis:
        return {}, {}

    conds = []
    if imos:
        conds.append(OfacSanctionedVessel.imo.in_(imos))
    if mmsis:
        conds.append(OfacSanctionedVessel.mmsi.in_(mmsis))

    rows = (await db.execute(
        select(OfacSanctionedVessel.imo, OfacSanctionedVessel.mmsi, OfacSanctionedVessel.program)
        .where(or_(*conds))
    )).all()

    by_imo: dict[int, str] = {}
    by_mmsi: dict[int, str] = {}
    for imo, mmsi, program in rows:
        prog = program or "OFAC SDN"
        if imo:
            by_imo[imo] = prog
        if mmsi:
            by_mmsi[mmsi] = prog
    return by_imo, by_mmsi


def _to_vessel_response(v: Vessel, by_imo: dict[int, str], by_mmsi: dict[int, str]) -> VesselResponse:
    resp = VesselResponse.model_validate(v)
    program = (by_imo.get(v.imo) if v.imo else None) or by_mmsi.get(v.mmsi)
    if program:
        resp.is_sanctioned = True
        resp.sanctions_program = program
    return resp


@router.get("", response_model=VesselListResponse)
async def list_vessels(
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="Filter: in_transit, at_port, dark"),
    seen_within_hours: Optional[float] = Query(6, ge=0, description="Only show vessels seen within this many hours. 0 = no filter."),
    lat_min: Optional[float] = Query(None, description="Viewport south bound"),
    lon_min: Optional[float] = Query(None, description="Viewport west bound"),
    lat_max: Optional[float] = Query(None, description="Viewport north bound"),
    lon_max: Optional[float] = Query(None, description="Viewport east bound"),
    db: AsyncSession = Depends(get_db),
):
    """List tracked tankers with latest positions. Supports spatial bbox filtering."""
    query = select(Vessel).where(Vessel.last_lat.isnot(None), Vessel.last_lon.isnot(None))

    # Filter by recency (default: last 1 hour)
    if seen_within_hours and seen_within_hours > 0:
        recency_cutoff = datetime.now(timezone.utc) - timedelta(hours=seen_within_hours)
        query = query.where(Vessel.last_seen >= recency_cutoff)

    if search:
        query = query.where(
            Vessel.name.ilike(f"%{search}%") | Vessel.mmsi.cast(String).contains(search)
        )

    if lat_min is not None and lon_min is not None and lat_max is not None and lon_max is not None:
        query = query.where(
            Vessel.last_lat.between(lat_min, lat_max),
            Vessel.last_lon.between(lon_min, lon_max),
        )

    if status == "in_transit":
        query = query.where(Vessel.last_speed > 3)
    elif status == "at_port":
        query = query.where(Vessel.last_speed <= 1)
    elif status == "dark":
        cutoff = datetime.now(timezone.utc) - timedelta(hours=6)
        query = query.where(Vessel.last_seen < cutoff)

    # Count total
    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    # Fetch page
    query = query.order_by(desc(Vessel.last_seen)).limit(limit).offset(offset)
    result = await db.execute(query)
    vessels = list(result.scalars().all())
    by_imo, by_mmsi = await _get_sanction_maps(db, vessels)

    return VesselListResponse(
        vessels=[_to_vessel_response(v, by_imo, by_mmsi) for v in vessels],
        total=total,
    )


@router.get("/{mmsi}", response_model=VesselResponse)
async def get_vessel(mmsi: int, db: AsyncSession = Depends(get_db)):
    """Get vessel detail with latest position."""
    result = await db.execute(select(Vessel).where(Vessel.mmsi == mmsi))
    vessel = result.scalar_one_or_none()
    if not vessel:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Vessel not found")
    by_imo, by_mmsi = await _get_sanction_maps(db, [vessel])
    return _to_vessel_response(vessel, by_imo, by_mmsi)


@router.get("/{mmsi}/trail", response_model=TrailResponse)
async def get_vessel_trail(
    mmsi: int,
    hours: int = Query(0, ge=0),
    start_time: Optional[datetime] = Query(None),
    end_time: Optional[datetime] = Query(None),
    max_points: int = Query(0, ge=0, le=10000),
    db: AsyncSession = Depends(get_db),
):
    """Get historical positions for a vessel with time filtering."""
    query = select(
        VesselPosition.time,
        VesselPosition.mmsi,
        VesselPosition.latitude,
        VesselPosition.longitude,
        VesselPosition.speed,
        VesselPosition.course,
        VesselPosition.heading,
        VesselPosition.draft,
        VesselPosition.source,
    ).where(VesselPosition.mmsi == mmsi)

    if start_time and end_time:
        query = query.where(VesselPosition.time.between(start_time, end_time))
    elif hours > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        query = query.where(VesselPosition.time > cutoff)

    query = query.order_by(VesselPosition.time.desc()).limit(10000)

    result = await db.execute(query)
    rows = list(result.all())
    rows.reverse()

    if max_points > 0 and len(rows) > max_points:
        step = max(1, len(rows) // max_points)
        sampled = list(rows[::step])
        if sampled[-1] != rows[-1]:
            sampled.append(rows[-1])
        rows = sampled

    return TrailResponse(
        mmsi=mmsi,
        positions=[
            PositionResponse(
                time=r.time,
                mmsi=r.mmsi,
                latitude=r.latitude,
                longitude=r.longitude,
                speed=r.speed,
                course=r.course,
                heading=r.heading,
                draft=r.draft,
                source=r.source,
            )
            for r in rows
        ],
    )


@router.get("/{mmsi}/cargo-events", response_model=list[CargoEventResponse])
async def get_vessel_cargo_events(
    mmsi: int,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Get cargo load/unload events for a vessel."""
    query = (
        select(CargoEvent)
        .where(CargoEvent.mmsi == mmsi)
        .order_by(desc(CargoEvent.created_at))
        .limit(limit)
    )
    result = await db.execute(query)
    events = result.scalars().all()
    return [CargoEventResponse.model_validate(e) for e in events]
