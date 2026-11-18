"""Market analytics API endpoints — ML-ready snapshots and oil prices."""

from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.global_oil_features import OilMarketSnapshot, OilPrice
from app.schemas.global_oil_features import MarketSnapshotResponse
from app.services.global_oil_feature_builder import GlobalOilFeatureBuilder

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

    # Fetch snapshots and oil prices separately and full-outer-merge by date so
    # today's latest price is always returned even before today's snapshot exists.
    snap_rows = (
        await db.execute(
            select(OilMarketSnapshot).where(OilMarketSnapshot.snapshot_date >= cutoff)
        )
    ).scalars().all()
    price_rows = (
        await db.execute(
            select(OilPrice).where(OilPrice.price_date >= cutoff)
        )
    ).scalars().all()

    snaps_by_date = {s.snapshot_date: s for s in snap_rows}
    prices_by_date = {p.price_date: p for p in price_rows}
    all_dates = sorted(set(snaps_by_date.keys()) | set(prices_by_date.keys()), reverse=True)

    out: list[MarketSnapshotResponse] = []
    for d in all_dates:
        s = snaps_by_date.get(d)
        p = prices_by_date.get(d)
        out.append(
            MarketSnapshotResponse(
                snapshot_date=d,
                total_active_vessels=s.total_active_vessels if s else None,
                vessels_in_transit=s.vessels_in_transit if s else None,
                vessels_at_port=s.vessels_at_port if s else None,
                avg_fleet_speed=s.avg_fleet_speed if s else None,
                vessels_idle_gt_48h=s.vessels_idle_gt_48h if s else None,
                dark_vessels_count=s.dark_vessels_count if s else None,
                new_ais_gaps_24h=s.new_ais_gaps_24h if s else None,
                resolved_gaps_24h=s.resolved_gaps_24h if s else None,
                avg_gap_duration_hours=s.avg_gap_duration_hours if s else None,
                sts_events_24h=s.sts_events_24h if s else None,
                sts_confirmed_24h=s.sts_confirmed_24h if s else None,
                chokepoint_transits_24h=s.chokepoint_transits_24h if s else None,
                strait_of_hormuz_transits=s.strait_of_hormuz_transits if s else None,
                cape_of_good_hope_transits=s.cape_of_good_hope_transits if s else None,
                bab_el_mandeb_transits=s.bab_el_mandeb_transits if s else None,
                suez_canal_transits=s.suez_canal_transits if s else None,
                strait_of_malacca_transits=s.strait_of_malacca_transits if s else None,
                panama_canal_transits=s.panama_canal_transits if s else None,
                turkish_straits_transits=s.turkish_straits_transits if s else None,
                danish_straits_transits=s.danish_straits_transits if s else None,
                other_straits_transits=s.other_straits_transits if s else None,
                other_straits_traffic_share_pct=s.other_straits_traffic_share_pct if s else None,
                avg_chokepoint_transit_min=s.avg_chokepoint_transit_min if s else None,
                cape_of_good_hope_avg_transit_min=s.cape_of_good_hope_avg_transit_min if s else None,
                other_straits_avg_transit_min=s.other_straits_avg_transit_min if s else None,
                hormuz_bab_avg_transit_min=s.hormuz_bab_avg_transit_min if s else None,
                portwatch_cape_tankers=s.portwatch_cape_tankers if s else None,
                portwatch_other_straits_tankers=s.portwatch_other_straits_tankers if s else None,
                cargo_events_24h=s.cargo_events_24h if s else None,
                estimated_volume_barrels_24h=s.estimated_volume_barrels_24h if s else None,
                brent_close_usd=p.brent_close_usd if p else None,
                wti_close_usd=p.wti_close_usd if p else None,
            )
        )
    return out


@router.post("/recalculate")
async def recalculate_global_oil_snapshots(
    days: int = Query(90, ge=1, le=365, description="Recalculate existing snapshots within the last N days"),
    db: AsyncSession = Depends(get_db),
):
    """Recalculate market feature snapshots for existing dates within the last N days (plus yesterday & today)."""
    now_utc = datetime.now(timezone.utc)
    cutoff = (now_utc - timedelta(days=days)).date()
    today = now_utc.date()
    yesterday = today - timedelta(days=1)

    existing_dates = (
        await db.execute(
            select(OilMarketSnapshot.snapshot_date)
            .where(OilMarketSnapshot.snapshot_date >= cutoff)
            .order_by(OilMarketSnapshot.snapshot_date.asc())
        )
    ).scalars().all()

    dates_to_recalc = sorted(set(existing_dates) | {yesterday, today})
    builder = GlobalOilFeatureBuilder()
    for target_date in dates_to_recalc:
        await builder.aggregate(target_date=target_date)

    return {"recalculated_days": len(dates_to_recalc)}
