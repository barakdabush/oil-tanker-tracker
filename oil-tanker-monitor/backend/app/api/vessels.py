"""Vessel API endpoints."""

from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, and_, desc, String
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Vessel, VesselPosition, CargoEvent
from app.schemas import VesselResponse, VesselListResponse, PositionResponse, TrailResponse, CargoEventResponse

router = APIRouter()


@router.get("", response_model=VesselListResponse)
async def list_vessels(
    limit: int = Query(500, ge=1, le=2000),
    offset: int = Query(0, ge=0),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="Filter: in_transit, at_port, dark"),
    lat_min: Optional[float] = Query(None, description="Viewport south bound"),
    lon_min: Optional[float] = Query(None, description="Viewport west bound"),
    lat_max: Optional[float] = Query(None, description="Viewport north bound"),
    lon_max: Optional[float] = Query(None, description="Viewport east bound"),
    db: AsyncSession = Depends(get_db),
):
    """List tracked tankers with latest positions. Supports spatial bbox filtering."""
    query = select(Vessel).where(Vessel.last_lat.isnot(None), Vessel.last_lon.isnot(None))

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
    vessels = result.scalars().all()

    return VesselListResponse(
        vessels=[VesselResponse.model_validate(v) for v in vessels],
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
    return VesselResponse.model_validate(vessel)


@router.get("/{mmsi}/trail", response_model=TrailResponse)
async def get_vessel_trail(
    mmsi: int,
    hours: int = Query(336, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """Get historical positions for a vessel."""
    if hours == 0:
        query = (
            select(VesselPosition)
            .where(VesselPosition.mmsi == mmsi)
            .order_by(VesselPosition.time.asc())
            .limit(50000)
        )
    else:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        query = (
            select(VesselPosition)
            .where(and_(VesselPosition.mmsi == mmsi, VesselPosition.time > cutoff))
            .order_by(VesselPosition.time.asc())
            .limit(5000)
        )
    result = await db.execute(query)
    positions = result.scalars().all()

    return TrailResponse(
        mmsi=mmsi,
        positions=[PositionResponse.model_validate(p) for p in positions],
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
