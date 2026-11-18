"""Daily feature aggregation service.

Collects 15 oil market features from existing vessel tracking data
and upserts them into the oil_market_snapshots table.
Runs daily at ~00:15 UTC via the worker.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta, date

from sqlalchemy import select, func, and_, or_, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import async_session
from app.models import Vessel, AISGap, STSEvent, ChokepointTransit, CargoEvent
from app.models.global_oil_features import OilMarketSnapshot

logger = logging.getLogger(__name__)

# 24 hours between runs
RUN_INTERVAL_SECONDS = 24 * 60 * 60
# Initial delay: ~15 minutes after midnight UTC
INITIAL_DELAY_SECONDS = 15 * 60


class GlobalOilFeatureBuilder:
    """Aggregates daily market features from existing vessel tracking data."""

    async def run_periodic(self):
        """Run feature aggregation once daily (backfilling recent missing days)."""
        # Small initial delay to let the system stabilise
        await asyncio.sleep(10)
        while True:
            try:
                await self.backfill_missing_days(days=7)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Feature aggregation error: {e}")
                await asyncio.sleep(5 * 60)
                continue
            await asyncio.sleep(RUN_INTERVAL_SECONDS)

    async def backfill_missing_days(self, days: int = 7):
        """Aggregate any missing dates in the last `days` days (up to yesterday UTC)."""
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
        start_date = yesterday - timedelta(days=days - 1)
        async with async_session() as session:
            existing_rows = (
                await session.execute(
                    select(OilMarketSnapshot.snapshot_date).where(
                        OilMarketSnapshot.snapshot_date >= start_date
                    )
                )
            ).scalars().all()
        existing = set(existing_rows)

        for offset in range(days - 1, -1, -1):
            d = yesterday - timedelta(days=offset)
            if d not in existing or d == yesterday:
                await self.aggregate(target_date=d)

    async def aggregate(self, target_date: date | None = None):
        """Aggregate all 15 features for the given date (defaults to yesterday UTC).

        Uses ON CONFLICT DO UPDATE so it is safe to re-run (idempotent).
        """
        start_time = time.time()

        if target_date is None:
            target_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()

        # Define the 24h window for the target date
        day_start = datetime(target_date.year, target_date.month, target_date.day, tzinfo=timezone.utc)
        day_end = day_start + timedelta(days=1)
        idle_cutoff = day_end - timedelta(hours=48)

        async with async_session() as session:
            # --- Fleet Activity (point-in-time from vessel_positions for [day_start, day_end)) ---
            fleet_row = (await session.execute(
                text("""
                    WITH daily_vessels AS (
                        SELECT DISTINCT ON (mmsi) mmsi, speed
                        FROM vessel_positions
                        WHERE time >= :day_start AND time < :day_end
                        ORDER BY mmsi, time DESC
                    )
                    SELECT
                        COUNT(*) AS total_active,
                        COUNT(*) FILTER (WHERE speed > 3) AS in_transit,
                        COUNT(*) FILTER (WHERE speed <= 1) AS at_port,
                        AVG(speed) AS avg_speed
                    FROM daily_vessels
                """),
                {"day_start": day_start, "day_end": day_end},
            )).one()

            total_active = int(fleet_row.total_active or 0)
            vessels_in_transit = int(fleet_row.in_transit or 0)
            vessels_at_port = int(fleet_row.at_port or 0)
            avg_fleet_speed = round(float(fleet_row.avg_speed), 2) if fleet_row.avg_speed is not None else None

            vessels_idle = (await session.execute(
                select(func.count()).select_from(Vessel).where(
                    and_(
                        Vessel.last_seen < idle_cutoff,
                        Vessel.last_seen >= day_end - timedelta(days=30),
                        Vessel.last_speed <= 1,
                    )
                )
            )).scalar() or 0

            # --- Dark Fleet / Sanctions Evasion ---
            # Count distinct vessels with a true dark gap (>= 6h) overlapping [day_start, day_end)
            dark_count = (await session.execute(
                select(func.count(func.distinct(AISGap.mmsi))).where(
                    and_(
                        or_(
                            AISGap.status.in_(["dark", "extended_dark"]),
                            and_(AISGap.status == "resolved", func.extract("epoch", AISGap.gap_end - AISGap.gap_start) / 3600 >= 6),
                        ),
                        AISGap.gap_start < day_end,
                        AISGap.gap_start >= day_start - timedelta(days=30),
                        or_(AISGap.gap_end.is_(None), AISGap.gap_end >= day_start),
                    )
                )
            )).scalar() or 0

            new_gaps = (await session.execute(
                select(func.count()).select_from(AISGap).where(
                    and_(AISGap.gap_start >= day_start, AISGap.gap_start < day_end)
                )
            )).scalar() or 0

            resolved_gaps = (await session.execute(
                select(func.count()).select_from(AISGap).where(
                    and_(
                        AISGap.gap_end >= day_start,
                        AISGap.gap_end < day_end,
                        AISGap.status == "resolved",
                    )
                )
            )).scalar() or 0

            avg_gap_hours_result = (await session.execute(
                select(
                    func.avg(
                        func.extract("epoch", AISGap.gap_end - AISGap.gap_start) / 3600
                    )
                ).where(
                    and_(
                        AISGap.gap_end >= day_start,
                        AISGap.gap_end < day_end,
                        AISGap.status == "resolved",
                    )
                )
            )).scalar()
            avg_gap_duration = round(float(avg_gap_hours_result), 2) if avg_gap_hours_result is not None else None

            # --- STS Transfers ---
            sts_count = (await session.execute(
                select(func.count()).select_from(STSEvent).where(
                    and_(STSEvent.created_at >= day_start, STSEvent.created_at < day_end)
                )
            )).scalar() or 0

            sts_confirmed = (await session.execute(
                select(func.count()).select_from(STSEvent).where(
                    and_(
                        STSEvent.created_at >= day_start,
                        STSEvent.created_at < day_end,
                        STSEvent.status == "confirmed",
                    )
                )
            )).scalar() or 0

            # --- Chokepoint Traffic ---
            chokepoint_total = (await session.execute(
                select(func.count()).select_from(ChokepointTransit).where(
                    and_(
                        ChokepointTransit.time >= day_start,
                        ChokepointTransit.time < day_end,
                    )
                )
            )).scalar() or 0

            # Hormuz transits — match by chokepoint name
            hormuz_count = (await session.execute(
                text("""
                    SELECT COUNT(*) FROM chokepoint_transits ct
                    JOIN chokepoints c ON ct.chokepoint_id = c.id
                    WHERE ct.time >= :day_start AND ct.time < :day_end
                      AND c.name = 'Strait of Hormuz'
                """),
                {"day_start": day_start, "day_end": day_end},
            )).scalar() or 0

            # --- Cargo & Volume ---
            cargo_count = (await session.execute(
                select(func.count()).select_from(CargoEvent).where(
                    and_(CargoEvent.created_at >= day_start, CargoEvent.created_at < day_end)
                )
            )).scalar() or 0

            volume_result = (await session.execute(
                select(func.coalesce(func.sum(CargoEvent.estimated_volume_barrels), 0)).where(
                    and_(CargoEvent.created_at >= day_start, CargoEvent.created_at < day_end)
                )
            )).scalar() or 0

            # --- Upsert snapshot ---
            values = {
                "snapshot_date": target_date,
                "total_active_vessels": total_active,
                "vessels_in_transit": vessels_in_transit,
                "vessels_at_port": vessels_at_port,
                "avg_fleet_speed": avg_fleet_speed,
                "vessels_idle_gt_48h": vessels_idle,
                "dark_vessels_count": dark_count,
                "new_ais_gaps_24h": new_gaps,
                "resolved_gaps_24h": resolved_gaps,
                "avg_gap_duration_hours": avg_gap_duration,
                "sts_events_24h": sts_count,
                "sts_confirmed_24h": sts_confirmed,
                "chokepoint_transits_24h": chokepoint_total,
                "strait_of_hormuz_transits": hormuz_count,
                "cargo_events_24h": cargo_count,
                "estimated_volume_barrels_24h": float(volume_result),
            }

            stmt = pg_insert(OilMarketSnapshot).values(**values)
            stmt = stmt.on_conflict_do_update(
                index_elements=["snapshot_date"],
                set_={k: v for k, v in values.items() if k != "snapshot_date"},
            )
            await session.execute(stmt)
            await session.commit()

        duration = time.time() - start_time
        logger.info(
            f"Feature aggregation complete for {target_date} in {duration:.1f}s — "
            f"active={total_active}, transit={vessels_in_transit}, dark={dark_count}, "
            f"STS={sts_count}, chokepoints={chokepoint_total}, cargo={cargo_count}"
        )
