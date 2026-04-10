"""Market analytics API endpoints — ML-ready snapshots and oil prices."""

from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.global_oil_features import OilMarketSnapshot, OilPrice
from app.schemas.global_oil_features import MarketSnapshotResponse

router = APIRouter()


@router.get("/snapshots", response_model=list[MarketSnapshotResponse])
async def get_global_oil_snapshots(
    days: int = Query(90, ge=1, le=730, description="Number of days of history to return"),
    db: AsyncSession = Depends(get_db),
):
    """Get joined market snapshots + oil prices for ML analysis.

    Returns daily rows with 15 fleet/market features and Brent/WTI prices.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).date()

    # Left join snapshots with prices on date
    query = (
        select(
            OilMarketSnapshot,
            OilPrice.brent_close_usd,
            OilPrice.wti_close_usd,
        )
        .outerjoin(OilPrice, OilMarketSnapshot.snapshot_date == OilPrice.price_date)
        .where(OilMarketSnapshot.snapshot_date >= cutoff)
        .order_by(desc(OilMarketSnapshot.snapshot_date))
    )

    result = await db.execute(query)
    rows = result.all()

    return [
        MarketSnapshotResponse(
            snapshot_date=row.OilMarketSnapshot.snapshot_date,
            total_active_vessels=row.OilMarketSnapshot.total_active_vessels,
            vessels_in_transit=row.OilMarketSnapshot.vessels_in_transit,
            vessels_at_port=row.OilMarketSnapshot.vessels_at_port,
            avg_fleet_speed=row.OilMarketSnapshot.avg_fleet_speed,
            vessels_idle_gt_48h=row.OilMarketSnapshot.vessels_idle_gt_48h,
            dark_vessels_count=row.OilMarketSnapshot.dark_vessels_count,
            new_ais_gaps_24h=row.OilMarketSnapshot.new_ais_gaps_24h,
            resolved_gaps_24h=row.OilMarketSnapshot.resolved_gaps_24h,
            avg_gap_duration_hours=row.OilMarketSnapshot.avg_gap_duration_hours,
            sts_events_24h=row.OilMarketSnapshot.sts_events_24h,
            sts_confirmed_24h=row.OilMarketSnapshot.sts_confirmed_24h,
            chokepoint_transits_24h=row.OilMarketSnapshot.chokepoint_transits_24h,
            strait_of_hormuz_transits=row.OilMarketSnapshot.strait_of_hormuz_transits,
            cargo_events_24h=row.OilMarketSnapshot.cargo_events_24h,
            estimated_volume_barrels_24h=row.OilMarketSnapshot.estimated_volume_barrels_24h,
            brent_close_usd=row.brent_close_usd,
            wti_close_usd=row.wti_close_usd,
        )
        for row in rows
    ]
