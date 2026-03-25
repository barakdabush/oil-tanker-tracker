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
    hours: int = 24,
    db: AsyncSession = Depends(get_db)
):
    """List all chokepoints and calculate their dynamic congestion status based on recent transits."""
    
    # Get all chokepoints with their GeoJSON boundaries
    chokepoints_stmt = text("SELECT id, name, region, avg_daily_oil_flow_mbd, congestion_threshold, ST_AsGeoJSON(boundary_polygon) as geojson FROM chokepoints")
    chokepoints_result = await db.execute(chokepoints_stmt)
    chokepoints = chokepoints_result.all()
    
    cutoff_time = datetime.now(timezone.utc) - timedelta(hours=hours)
    
    # Calculate transit counts in the selected time window natively
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
                boundary_geojson=json.loads(cp.geojson) if cp.geojson else None
            )
        )
        
    return response

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
