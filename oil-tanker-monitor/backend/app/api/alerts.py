"""Alerts API endpoints — dark fleet, STS events, chokepoint status."""

from typing import Optional
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func, and_, desc, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import AISGap, STSEvent, Chokepoint, ChokepointTransit, Vessel
from app.schemas import (
    AISGapResponse, STSEventResponse,
    ChokepointResponse, ChokepointTransitResponse,
)

router = APIRouter()


# ─── Dark Fleet ─────────────────────────────────────────────────────────────────

@router.get("/dark-fleet", response_model=list[AISGapResponse])
async def list_dark_fleet(
    status: Optional[str] = Query(None, description="monitoring, dark, extended_dark, resolved"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """List vessels currently dark or with recent AIS gaps."""
    query = select(AISGap)

    if status:
        query = query.where(AISGap.status == status)
    else:
        query = query.where(AISGap.status != "resolved")

    query = query.order_by(desc(AISGap.gap_start)).limit(limit)
    result = await db.execute(query)
    gaps = result.scalars().all()
    return [AISGapResponse.model_validate(g) for g in gaps]


@router.get("/dark-fleet/{mmsi}/history", response_model=list[AISGapResponse])
async def get_dark_fleet_history(
    mmsi: int,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """Get AIS gap history for a specific vessel."""
    query = (
        select(AISGap)
        .where(AISGap.mmsi == mmsi)
        .order_by(desc(AISGap.gap_start))
        .limit(limit)
    )
    result = await db.execute(query)
    gaps = result.scalars().all()
    return [AISGapResponse.model_validate(g) for g in gaps]


# ─── STS Events ────────────────────────────────────────────────────────────────

@router.get("/sts-events", response_model=list[STSEventResponse])
async def list_sts_events(
    status: Optional[str] = Query(None, description="detected, ongoing, confirmed, dismissed"),
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """List recent ship-to-ship transfer events."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    query = select(STSEvent).where(STSEvent.start_time > cutoff)

    if status:
        query = query.where(STSEvent.status == status)

    query = query.order_by(desc(STSEvent.start_time)).limit(limit)
    result = await db.execute(query)
    events = result.scalars().all()
    return [STSEventResponse.model_validate(e) for e in events]


@router.get("/sts-events/{event_id}", response_model=STSEventResponse)
async def get_sts_event(event_id: str, db: AsyncSession = Depends(get_db)):
    """Get detail of a specific STS encounter."""
    result = await db.execute(select(STSEvent).where(STSEvent.id == event_id))
    event = result.scalar_one_or_none()
    if not event:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="STS event not found")
    return STSEventResponse.model_validate(event)


# ─── Chokepoints ────────────────────────────────────────────────────────────────

@router.get("/chokepoints", response_model=list[ChokepointResponse])
async def list_chokepoints(db: AsyncSession = Depends(get_db)):
    """Get current status of all chokepoints with live vessel counts."""
    chokepoints_q = select(Chokepoint)
    result = await db.execute(chokepoints_q)
    chokepoints = result.scalars().all()

    response = []
    for cp in chokepoints:
        # Count vessels currently inside (have entry but no exit)
        count_q = select(func.count()).where(
            and_(
                ChokepointTransit.chokepoint_id == cp.id,
                ChokepointTransit.exit_time.is_(None),
            )
        )
        count_result = await db.execute(count_q)
        current_count = count_result.scalar() or 0

        threshold = cp.congestion_threshold or 20
        if current_count > threshold * 1.5:
            congestion = "congested"
        elif current_count < threshold * 0.5:
            congestion = "low"
        else:
            congestion = "normal"

        cp_response = ChokepointResponse(
            id=cp.id,
            name=cp.name,
            region=cp.region,
            avg_daily_oil_flow_mbd=cp.avg_daily_oil_flow_mbd,
            congestion_threshold=cp.congestion_threshold,
            current_vessel_count=current_count,
            congestion_status=congestion,
        )
        response.append(cp_response)

    return response


@router.get("/chokepoints/{cp_id}/transits", response_model=list[ChokepointTransitResponse])
async def get_chokepoint_transits(
    cp_id: int,
    hours: int = Query(24, ge=1, le=168),
    limit: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Get recent transits through a chokepoint."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    query = (
        select(ChokepointTransit)
        .where(and_(
            ChokepointTransit.chokepoint_id == cp_id,
            ChokepointTransit.entry_time > cutoff,
        ))
        .order_by(desc(ChokepointTransit.entry_time))
        .limit(limit)
    )
    result = await db.execute(query)
    transits = result.scalars().all()
    return [ChokepointTransitResponse.model_validate(t) for t in transits]


@router.get("/chokepoints/{cp_id}/stats")
async def get_chokepoint_stats(
    cp_id: int,
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """Get historical transit stats for a chokepoint."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Daily transit counts
    from sqlalchemy import cast, Date
    daily_q = (
        select(
            func.date_trunc("day", ChokepointTransit.entry_time).label("date"),
            func.count().label("transit_count"),
            func.avg(ChokepointTransit.transit_duration_min).label("avg_duration_min"),
        )
        .where(and_(
            ChokepointTransit.chokepoint_id == cp_id,
            ChokepointTransit.entry_time > cutoff,
        ))
        .group_by("date")
        .order_by("date")
    )
    result = await db.execute(daily_q)
    rows = result.all()

    return {
        "chokepoint_id": cp_id,
        "period_days": days,
        "daily_stats": [
            {
                "date": str(row.date.date()) if row.date else None,
                "transit_count": row.transit_count,
                "avg_duration_min": round(row.avg_duration_min, 1) if row.avg_duration_min else None,
            }
            for row in rows
        ],
    }
