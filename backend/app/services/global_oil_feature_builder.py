"""Daily feature aggregation service.

Collects 15 oil market features from existing vessel tracking data
and upserts them into the oil_market_snapshots table.
Runs daily at ~00:15 UTC via the worker.
"""

import asyncio
import logging
import math
import time
from datetime import datetime, timezone, timedelta, date

from sqlalchemy import select, func, and_, or_, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import async_session
from app.models import Vessel, AISGap, STSEvent, ChokepointTransit, CargoEvent
from app.models.global_oil_features import OilMarketSnapshot
from app.models.news import NewsEvent, NewsImpactPrior
from app.services.news_clusterer import EVENT_CATEGORIES

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

            # --- Operational States & Port Congestion ---
            state_row = (await session.execute(
                text("""
                    SELECT
                        COUNT(DISTINCT mmsi) FILTER (WHERE state = 'anchored_waiting') AS anchored_waiting,
                        COUNT(DISTINCT mmsi) FILTER (WHERE state = 'floating_storage') AS floating_storage,
                        COUNT(DISTINCT mmsi) FILTER (WHERE state = 'in_transit_laden') AS in_transit_laden,
                        COUNT(DISTINCT mmsi) FILTER (WHERE state = 'in_transit_ballast') AS in_transit_ballast
                    FROM vessel_state_history
                    WHERE started_at < :day_end
                      AND (ended_at IS NULL OR ended_at >= :day_start)
                """),
                {"day_start": day_start, "day_end": day_end},
            )).one()

            anchored_waiting_count = int(getattr(state_row, "anchored_waiting", 0) or 0)
            floating_storage_count = int(getattr(state_row, "floating_storage", 0) or 0)
            in_transit_laden_count = int(getattr(state_row, "in_transit_laden", 0) or 0)
            in_transit_ballast_count = int(getattr(state_row, "in_transit_ballast", 0) or 0)

            port_row = (await session.execute(
                text("""
                    SELECT
                        AVG(avg_wait_hours) FILTER (WHERE anchored_waiting_count > 0) AS avg_wait,
                        AVG(utilization_pct) AS avg_util
                    FROM port_daily_stats
                    WHERE stat_date = :target_date
                """),
                {"target_date": target_date},
            )).one()
            avg_port_wait_hours = (
                round(float(port_row.avg_wait), 2)
                if getattr(port_row, "avg_wait", None) is not None
                else None
            )
            port_congestion_pct = (
                round(float(port_row.avg_util), 1)
                if getattr(port_row, "avg_util", None) is not None
                else None
            )

            # --- News & Event Intelligence + AIS x News Interactions (strictly <= day_end) ---
            news_feats = await self._compute_news_features(
                session=session,
                target_date=target_date,
                day_start=day_start,
                day_end=day_end,
                hormuz_transits=hormuz_count,
                new_ais_gaps=new_gaps,
            )

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
                "anchored_waiting_count": anchored_waiting_count,
                "avg_port_wait_hours": avg_port_wait_hours,
                "port_congestion_pct": port_congestion_pct,
                "floating_storage_count": floating_storage_count,
                "in_transit_laden_count": in_transit_laden_count,
                "in_transit_ballast_count": in_transit_ballast_count,
                **news_feats,
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

    async def _compute_news_features(
        self,
        session,
        target_date: date,
        day_start: datetime,
        day_end: datetime,
        hormuz_transits: int,
        new_ais_gaps: int,
    ) -> dict:
        """Compute daily decayed news pressure and AIS x News interaction features (zero look-ahead)."""
        empty = {
            "news_event_count_24h": None,
            "news_max_rank_24h": None,
            "news_net_impact_1d": None,
            "news_net_impact_7d": None,
            "news_supply_risk_score": None,
            "news_sanctions_pressure_7d": None,
            "news_opec_signal_7d": None,
            "news_hormuz_x_transit_drop": None,
            "news_sanctions_x_dark_fleet": None,
        }
        try:
            lookback_start = day_end - timedelta(days=14)
            res = await session.execute(
                select(NewsEvent).where(
                    and_(
                        NewsEvent.first_seen_at >= lookback_start,
                        NewsEvent.first_seen_at < day_end,
                    )
                )
            )
            events = [
                e for e in (res.scalars().all() or [])
                if hasattr(e, "first_seen_at") and isinstance(e.first_seen_at, datetime)
            ]
        except Exception:
            return empty

        if not events:
            return empty

        # Walk-forward priors strictly on or before target_date (anti-leakage)
        priors_1d: dict[str, float] = {}
        priors_7d: dict[str, float] = {}
        half_lives: dict[str, float] = {}
        try:
            latest_prior_date = (
                await session.execute(
                    select(func.max(NewsImpactPrior.as_of_date)).where(
                        and_(
                            NewsImpactPrior.as_of_date <= target_date,
                            NewsImpactPrior.symbol == "BZ=F",
                        )
                    )
                )
            ).scalar()
            if isinstance(latest_prior_date, date):
                p_rows = (
                    await session.execute(
                        select(NewsImpactPrior).where(
                            and_(
                                NewsImpactPrior.as_of_date == latest_prior_date,
                                NewsImpactPrior.symbol == "BZ=F",
                                NewsImpactPrior.region_scope == "global",
                            )
                        )
                    )
                ).scalars().all()
                for pr in p_rows or []:
                    if pr.horizon == "24h" and pr.shrunk_weight is not None:
                        priors_1d[pr.event_type] = float(pr.shrunk_weight)
                    elif pr.horizon == "7d" and pr.shrunk_weight is not None:
                        priors_7d[pr.event_type] = float(pr.shrunk_weight)
                    if pr.half_life_hours:
                        half_lives[pr.event_type] = float(pr.half_life_hours)
        except Exception:
            pass

        events_24h = [e for e in events if e.first_seen_at >= day_start]
        count_24h = len(events_24h)
        max_rank_24h = round(max((float(e.prior_rank_score or 0.0) for e in events_24h), default=0.0), 2)

        net_1d = 0.0
        net_7d = 0.0
        supply_risk = 0.0
        sanctions_7d = 0.0
        opec_7d = 0.0
        hormuz_news = 0.0

        supply_cats = {"chokepoint_attack_blockade", "refinery_pipeline_outage", "geopolitical_escalation"}

        for ev in events:
            age_h = max(0.0, (day_end - ev.first_seen_at).total_seconds() / 3600.0)
            cat = ev.event_type
            default_hl = EVENT_CATEGORIES.get(cat, EVENT_CATEGORIES["geopolitical_escalation"]).default_half_life_hours
            hl = max(6.0, half_lives.get(cat, default_hl))
            decay = math.exp(-math.log(2.0) * age_h / hl)

            rank_w = float(ev.prior_rank_score or 0.0) / 100.0
            direction = float(ev.effective_direction or 0.0)

            # Weight by learned shrunk_weight magnitude if available, else 1.0 prior
            w1 = max(0.25, abs(priors_1d.get(cat, 0.0))) if cat in priors_1d else 1.0
            w7 = max(0.25, abs(priors_7d.get(cat, 0.0))) if cat in priors_7d else 1.0

            net_1d += direction * rank_w * w1 * decay
            net_7d += direction * rank_w * w7 * math.exp(-math.log(2.0) * age_h / max(hl, 120.0))

            if cat in supply_cats and age_h <= 72.0:
                supply_risk += max(0.0, direction) * rank_w * decay
            if cat == "sanctions_enforcement" and age_h <= 168.0:
                sanctions_7d += rank_w * decay
            if cat == "opec_production_decision" and age_h <= 168.0:
                opec_7d += direction * rank_w * decay
            if "Strait of Hormuz" in (ev.chokepoints or []) and age_h <= 72.0:
                hormuz_news += max(0.0, direction) * rank_w * decay

        # AIS x News confirmation interactions
        # Compare today's Hormuz transits against trailing 14d baseline if available
        hormuz_baseline = None
        try:
            hb = (
                await session.execute(
                    select(func.avg(OilMarketSnapshot.strait_of_hormuz_transits)).where(
                        and_(
                            OilMarketSnapshot.snapshot_date >= target_date - timedelta(days=14),
                            OilMarketSnapshot.snapshot_date < target_date,
                        )
                    )
                )
            ).scalar()
            if isinstance(hb, (int, float)) and hb > 0:
                hormuz_baseline = float(hb)
        except Exception:
            pass

        if hormuz_baseline and hormuz_baseline > 0:
            transit_drop_pct = max(0.0, (hormuz_baseline - hormuz_transits) / hormuz_baseline)
            hormuz_x_drop = round(hormuz_news * transit_drop_pct * 100.0, 3)
        else:
            hormuz_x_drop = 0.0

        sanctions_x_dark = round(sanctions_7d * math.log1p(max(0, new_ais_gaps)), 3)

        return {
            "news_event_count_24h": count_24h,
            "news_max_rank_24h": max_rank_24h,
            "news_net_impact_1d": round(net_1d, 4),
            "news_net_impact_7d": round(net_7d, 4),
            "news_supply_risk_score": round(supply_risk, 4),
            "news_sanctions_pressure_7d": round(sanctions_7d, 4),
            "news_opec_signal_7d": round(opec_7d, 4),
            "news_hormuz_x_transit_drop": hormuz_x_drop,
            "news_sanctions_x_dark_fleet": sanctions_x_dark,
        }

