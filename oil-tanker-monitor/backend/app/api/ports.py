"""Port API endpoints."""

from typing import Optional
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Port, CargoEvent, Vessel
from app.schemas import PortResponse, PortListResponse, CargoEventResponse

router = APIRouter()


@router.get("", response_model=PortListResponse)
async def list_ports(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    region: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """List oil ports and terminals."""
    query = select(Port)

    if search:
        query = query.where(Port.name.ilike(f"%{search}%"))
    if region:
        query = query.where(Port.region.ilike(f"%{region}%"))

    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    query = query.order_by(Port.name).limit(limit).offset(offset)
    result = await db.execute(query)
    ports = result.scalars().all()

    return PortListResponse(
        ports=[PortResponse.model_validate(p) for p in ports],
        total=total,
    )


@router.get("/{port_id}", response_model=PortResponse)
async def get_port(port_id: int, db: AsyncSession = Depends(get_db)):
    """Get port detail."""
    result = await db.execute(select(Port).where(Port.id == port_id))
    port = result.scalar_one_or_none()
    if not port:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Port not found")
    return PortResponse.model_validate(port)


@router.get("/{port_id}/activity", response_model=list[CargoEventResponse])
async def get_port_activity(
    port_id: int,
    days: int = Query(7, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
):
    """Get recent vessel activity at a port."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    query = (
        select(CargoEvent)
        .where(and_(CargoEvent.port_id == port_id, CargoEvent.created_at > cutoff))
        .order_by(desc(CargoEvent.created_at))
        .limit(100)
    )
    result = await db.execute(query)
    events = result.scalars().all()
    return [CargoEventResponse.model_validate(e) for e in events]
