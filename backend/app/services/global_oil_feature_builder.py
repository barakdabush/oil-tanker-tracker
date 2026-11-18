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

# 1 hour between runs (keeps today's intraday vessel features up to date)
RUN_INTERVAL_SECONDS = 60 * 60
# Initial delay: ~15 minutes after midnight UTC
INITIAL_DELAY_SECONDS = 15 * 60


class GlobalOilFeatureBuilder:
    """Aggregates daily market features from existing vessel tracking data."""

    async def run_periodic(self):
        """Run feature aggregation hourly (backfilling recent missing days + today)."""
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

    async def backfill_missing_days(self, days: int = 7, include_today: bool = True):
        """Aggregate any missing dates in the last `days` days plus yesterday and today UTC."""
        today = datetime.now(timezone.utc).date()
        yesterday = today - timedelta(days=1)
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
        if include_today:
            await self.aggregate(target_date=today)

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

            # --- Chokepoint Traffic & Delays ---
            cp_row = (await session.execute(
                text("""
                    SELECT
                        COUNT(*) AS total_transits,
                        COUNT(*) FILTER (WHERE c.name = 'Strait of Hormuz') AS hormuz_transits,
                        COUNT(*) FILTER (WHERE c.name = 'Cape of Good Hope') AS cape_transits,
                        COUNT(*) FILTER (WHERE c.name = 'Bab el-Mandeb') AS bab_transits,
                        COUNT(*) FILTER (WHERE c.name = 'Suez Canal') AS suez_transits,
                        COUNT(*) FILTER (WHERE c.name = 'Strait of Malacca') AS malacca_transits,
                        COUNT(*) FILTER (WHERE c.name = 'Panama Canal') AS panama_transits,
                        COUNT(*) FILTER (WHERE c.name = 'Turkish Straits (Bosporus)') AS turkish_transits,
                        COUNT(*) FILTER (WHERE c.name = 'Danish Straits') AS danish_transits,
                        COUNT(*) FILTER (
                            WHERE c.name NOT IN ('Strait of Hormuz', 'Bab el-Mandeb')
                        ) AS other_transits,
                        AVG(ct.transit_duration_min) FILTER (
                            WHERE ct.transit_duration_min IS NOT NULL
                        ) AS avg_transit_min,
                        AVG(ct.transit_duration_min) FILTER (
                            WHERE c.name = 'Cape of Good Hope' AND ct.transit_duration_min IS NOT NULL
                        ) AS cape_avg_transit_min,
                        AVG(ct.transit_duration_min) FILTER (
                            WHERE c.name NOT IN ('Strait of Hormuz', 'Bab el-Mandeb')
                              AND ct.transit_duration_min IS NOT NULL
                        ) AS other_avg_transit_min,
                        AVG(ct.transit_duration_min) FILTER (
                            WHERE c.name IN ('Strait of Hormuz', 'Bab el-Mandeb')
                              AND ct.transit_duration_min IS NOT NULL
                        ) AS hormuz_bab_avg_transit_min
                    FROM chokepoint_transits ct
                    JOIN chokepoints c ON ct.chokepoint_id = c.id
                    WHERE ct.time >= :day_start AND ct.time < :day_end
                """),
                {"day_start": day_start, "day_end": day_end},
            )).one()

            chokepoint_total = int(getattr(cp_row, "total_transits", 0) or 0)
            hormuz_count = int(getattr(cp_row, "hormuz_transits", 0) or 0)
            cape_count = int(getattr(cp_row, "cape_transits", 0) or 0)
            bab_count = int(getattr(cp_row, "bab_transits", 0) or 0)
            suez_count = int(getattr(cp_row, "suez_transits", 0) or 0)
            malacca_count = int(getattr(cp_row, "malacca_transits", 0) or 0)
            panama_count = int(getattr(cp_row, "panama_transits", 0) or 0)
            turkish_count = int(getattr(cp_row, "turkish_transits", 0) or 0)
            danish_count = int(getattr(cp_row, "danish_transits", 0) or 0)
            other_straits_count = int(getattr(cp_row, "other_transits", 0) or 0)
            other_straits_share = (
                round((other_straits_count / chokepoint_total) * 100.0, 2)
                if chokepoint_total > 0
                else None
            )

            def _round_opt(val) -> float | None:
                return round(float(val), 2) if val is not None else None

            avg_chokepoint_transit_min = _round_opt(getattr(cp_row, "avg_transit_min", None))
            cape_avg_transit_min = _round_opt(getattr(cp_row, "cape_avg_transit_min", None))
            other_avg_transit_min = _round_opt(getattr(cp_row, "other_avg_transit_min", None))
            hormuz_bab_avg_transit_min = _round_opt(getattr(cp_row, "hormuz_bab_avg_transit_min", None))

            # --- IMF PortWatch Satellite-AIS Tanker Counts (nullable when lagged) ---
            pw_row = (await session.execute(
                text("""
                    SELECT
                        SUM(n_tanker) FILTER (WHERE portname ILIKE '%Cape of Good Hope%') AS pw_cape_tankers,
                        SUM(n_tanker) FILTER (
                            WHERE portname NOT ILIKE '%Hormuz%'
                              AND portname NOT ILIKE '%Mandeb%'
                        ) AS pw_other_tankers
                    FROM portwatch_chokepoint_daily
                    WHERE date = :target_date
                """),
                {"target_date": target_date},
            )).one()
            pw_cape_raw = getattr(pw_row, "pw_cape_tankers", None)
            pw_other_raw = getattr(pw_row, "pw_other_tankers", None)
            portwatch_cape_tankers = int(pw_cape_raw) if pw_cape_raw is not None else None
            portwatch_other_straits_tankers = int(pw_other_raw) if pw_other_raw is not None else None

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
                "cape_of_good_hope_transits": cape_count,
                "bab_el_mandeb_transits": bab_count,
                "suez_canal_transits": suez_count,
                "strait_of_malacca_transits": malacca_count,
                "panama_canal_transits": panama_count,
                "turkish_straits_transits": turkish_count,
                "danish_straits_transits": danish_count,
                "other_straits_transits": other_straits_count,
                "other_straits_traffic_share_pct": other_straits_share,
                "avg_chokepoint_transit_min": avg_chokepoint_transit_min,
                "cape_of_good_hope_avg_transit_min": cape_avg_transit_min,
                "other_straits_avg_transit_min": other_avg_transit_min,
                "hormuz_bab_avg_transit_min": hormuz_bab_avg_transit_min,
                "portwatch_cape_tankers": portwatch_cape_tankers,
                "portwatch_other_straits_tankers": portwatch_other_straits_tankers,
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
