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


@router.get("/congestion")
async def get_port_congestion(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Return ports ranked by current tanker fullness & anchorage queue."""
    from sqlalchemy import text
    rows = (
        await db.execute(
            text("""
                SELECT
                    p.id,
                    p.name,
                    p.country,
                    p.region,
                    p.port_type,
                    COUNT(v.mmsi) FILTER (WHERE v.operational_state = 'at_port') AS berthed_count,
                    COUNT(v.mmsi) FILTER (WHERE v.operational_state = 'anchored_waiting') AS anchored_waiting_count,
                    ROUND(AVG(EXTRACT(EPOCH FROM (NOW() - v.state_since)) / 3600.0)
                        FILTER (WHERE v.operational_state = 'anchored_waiting')::numeric, 1) AS avg_wait_hours,
                    pds.utilization_pct
                FROM ports p
                LEFT JOIN vessels v
                    ON v.current_port_id = p.id
                   AND v.operational_state IN ('at_port', 'anchored_waiting')
                   AND v.last_seen >= NOW() - INTERVAL '48 hours'
                LEFT JOIN port_daily_stats pds
                    ON pds.port_id = p.id
                   AND pds.stat_date = (NOW() AT TIME ZONE 'UTC')::date
                GROUP BY p.id, p.name, p.country, p.region, p.port_type, pds.utilization_pct
                HAVING COUNT(v.mmsi) > 0 OR pds.utilization_pct IS NOT NULL
                ORDER BY (
                    COUNT(v.mmsi) FILTER (WHERE v.operational_state = 'anchored_waiting') * 2
                    + COUNT(v.mmsi) FILTER (WHERE v.operational_state = 'at_port')
                ) DESC, p.name ASC
                LIMIT :limit
            """),
            {"limit": limit},
        )
    ).all()

    return [
        {
            "port_id": r.id,
            "name": r.name,
            "country": r.country,
            "region": r.region,
            "port_type": r.port_type,
            "berthed_count": int(r.berthed_count or 0),
            "anchored_waiting_count": int(r.anchored_waiting_count or 0),
            "avg_wait_hours": float(r.avg_wait_hours) if r.avg_wait_hours is not None else None,
            "utilization_pct": float(r.utilization_pct) if r.utilization_pct is not None else None,
        }
        for r in rows
    ]


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
