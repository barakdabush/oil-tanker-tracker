"""Multi-horizon event-study analyzer & walk-forward Empirical Bayes lag learner.

For every canonical NewsEvent and symbol (BZ=F Brent, CL=F WTI):
1. Measures pre-event drift (-24h, -4h) and post-event reactions (+1h, +4h, +24h, +3d, +7d, +14d)
   as both raw log-returns and volatility-normalized z-scores.
2. Stores materialized outcomes in ``news_event_price_impacts``.
3. Aggregates walk-forward Empirical Bayes statistics into ``news_impact_priors``:
   - mean_ret, median_ret, mean_z, directional hit_rate, t_stat, shrunk_weight = n/(n+k) * mean_z
   - estimated decay half_life_hours per category so downstream ML features know how long
     each event type persists vs. reverts.
4. Updates ``news_events.learned_impact_score`` using the latest out-of-sample priors.
"""

from __future__ import annotations

import asyncio
import bisect
import logging
import math
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import async_session
from app.models.global_oil_features import OilPrice
from app.models.news import (
    NewsEvent,
    NewsEventPriceImpact,
    NewsImpactPrior,
    OilPriceIntraday,
)
from app.services.news_clusterer import EVENT_CATEGORIES

logger = logging.getLogger(__name__)

SYMBOLS = ("BZ=F", "CL=F")
SHRINKAGE_K = 15.0  # Empirical Bayes shrinkage pseudocount
DEFAULT_DAILY_VOL = 0.02  # 2% daily vol fallback if trailing 20d vol unavailable
RUN_INTERVAL_SECONDS = 60 * 60

# (horizon_code, offset_hours, ret_col, z_col)
HORIZON_SPECS: list[tuple[str, int, str, Optional[str]]] = [
    ("pre_24h", -24, "ret_pre_24h", None),
    ("pre_4h", -4, "ret_pre_4h", None),
    ("1h", 1, "ret_1h", "z_1h"),
    ("4h", 4, "ret_4h", "z_4h"),
    ("24h", 24, "ret_24h", "z_24h"),
    ("3d", 72, "ret_3d", "z_3d"),
    ("7d", 168, "ret_7d", "z_7d"),
    ("14d", 336, "ret_14d", "z_14d"),
]

POST_HORIZONS = ("1h", "4h", "24h", "3d", "7d", "14d")
HORIZON_HOURS = {h: hrs for h, hrs, _, _ in HORIZON_SPECS}


# ---------------------------------------------------------------------------
# Pure Statistical Helpers (unit tested)
# ---------------------------------------------------------------------------

def compute_z_score(log_ret: float, daily_vol: float, horizon_hours: int) -> float:
    """Normalize a log-return by trailing daily volatility scaled by sqrt(h / 24)."""
    vol = daily_vol if (daily_vol and daily_vol > 1e-6) else DEFAULT_DAILY_VOL
    scale = math.sqrt(max(1.0, abs(horizon_hours)) / 24.0)
    return round(log_ret / (vol * scale), 4)


def compute_empirical_bayes_stats(
    rets: list[float],
    zs: list[float],
    directions: list[float],
    shrinkage_k: float = SHRINKAGE_K,
) -> dict:
    """Compute sample_count, mean_ret, median_ret, mean_z, hit_rate, t_stat, shrunk_weight."""
    n = len(rets)
    if n == 0:
        return {
            "sample_count": 0,
            "mean_ret": None,
            "median_ret": None,
            "mean_z": None,
            "hit_rate": None,
            "t_stat": None,
            "shrunk_weight": 0.0,
        }

    mean_ret = sum(rets) / n
    median_ret = statistics.median(rets)
    mean_z = sum(zs) / len(zs) if zs else 0.0

    # Directional hit rate: only evaluate events with non-trivial expected direction
    dir_pairs = [(r, d) for r, d in zip(rets, directions) if abs(d) >= 0.15]
    if dir_pairs:
        hits = sum(1 for r, d in dir_pairs if (r > 0 and d > 0) or (r < 0 and d < 0))
        hit_rate = round(hits / len(dir_pairs), 4)
    else:
        hit_rate = None

    if len(zs) >= 2:
        std_z = statistics.stdev(zs)
        t_stat = round(mean_z / (std_z / math.sqrt(len(zs))), 3) if std_z > 1e-6 else 0.0
    else:
        t_stat = 0.0

    shrunk_weight = round((n / (n + shrinkage_k)) * mean_z, 4)

    return {
        "sample_count": n,
        "mean_ret": round(mean_ret, 6),
        "median_ret": round(median_ret, 6),
        "mean_z": round(mean_z, 4),
        "hit_rate": hit_rate,
        "t_stat": t_stat,
        "shrunk_weight": shrunk_weight,
    }


def estimate_half_life_hours(horizon_abs_z: dict[str, float], default_hours: float) -> float:
    """Estimate signal decay half-life from the peak post-event horizon."""
    valid = {h: abs(z) for h, z in horizon_abs_z.items() if h in POST_HORIZONS and z is not None}
    if not valid:
        return default_hours
    peak_h = max(valid, key=lambda h: valid[h])
    peak_val = valid[peak_h]
    peak_hrs = HORIZON_HOURS[peak_h]
    if peak_val < 0.05:
        return default_hours

    # Look for the first horizon after peak where magnitude drops below half of peak
    for h in POST_HORIZONS:
        hrs = HORIZON_HOURS[h]
        if hrs > peak_hrs and h in valid and valid[h] <= 0.5 * peak_val:
            return float(max(6.0, hrs - peak_hrs))
    # Signal persists through the longest observed horizon
    return float(max(default_hours, peak_hrs * 1.5))


def _lookup_price_at(
    times: list[datetime],
    closes: list[float],
    vols: list[Optional[float]],
    target: datetime,
    max_gap_hours: float = 72.0,
) -> tuple[Optional[float], Optional[float]]:
    """Binary-search the closest price bar at or just before/after target within max_gap_hours."""
    if not times:
        return None, None
    idx = bisect.bisect_left(times, target)
    candidates: list[tuple[float, int]] = []
    if idx < len(times):
        diff = abs((times[idx] - target).total_seconds()) / 3600.0
        if diff <= max_gap_hours:
            candidates.append((diff, idx))
    if idx > 0:
        diff = abs((times[idx - 1] - target).total_seconds()) / 3600.0
        if diff <= max_gap_hours:
            candidates.append((diff, idx - 1))
    if not candidates:
        return None, None
    _, best_idx = min(candidates, key=lambda x: x[0])
    return closes[best_idx], vols[best_idx]


class NewsImpactAnalyzer:
    """Computes multi-horizon event price impacts and updates walk-forward learned priors."""

    async def run_periodic(self):
        await asyncio.sleep(70)
        while True:
            try:
                await self.run_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"NewsImpactAnalyzer error: {type(e).__name__}: {e}")
                await asyncio.sleep(10 * 60)
                continue
            await asyncio.sleep(RUN_INTERVAL_SECONDS)

    async def run_once(self) -> dict[str, int]:
        impacts_updated = await self.compute_event_impacts()
        priors_updated = await self.update_impact_priors()
        return {"impacts_updated": impacts_updated, "priors_updated": priors_updated}

    async def compute_event_impacts(self, lookback_days: int = 365) -> int:
        """Compute/update multi-horizon price impacts for events whose horizons have matured."""
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=lookback_days)

        async with async_session() as session:
            events = list(
                (
                    await session.execute(
                        select(NewsEvent)
                        .where(NewsEvent.market_aligned_at >= cutoff)
                        .order_by(NewsEvent.market_aligned_at.asc())
                    )
                ).scalars().all()
            )
            if not events:
                return 0

            # Load intraday 1h bars for BZ=F and CL=F
            intraday_series: dict[str, tuple[list[datetime], list[float], list[Optional[float]]]] = {}
            for sym in SYMBOLS:
                rows = (
                    await session.execute(
                        select(
                            OilPriceIntraday.bar_time,
                            OilPriceIntraday.close,
                            OilPriceIntraday.realized_vol_20d,
                        )
                        .where(OilPriceIntraday.symbol == sym)
                        .order_by(OilPriceIntraday.bar_time.asc())
                    )
                ).all()
                intraday_series[sym] = (
                    [r.bar_time for r in rows],
                    [r.close for r in rows],
                    [r.realized_vol_20d for r in rows],
                )

            # Daily fallback series from oil_prices for multi-day horizons if intraday is sparse
            daily_rows = (
                await session.execute(select(OilPrice).order_by(OilPrice.price_date.asc()))
            ).scalars().all()
            daily_series: dict[str, tuple[list[datetime], list[float], list[Optional[float]]]] = {
                "BZ=F": (
                    [datetime(r.price_date.year, r.price_date.month, r.price_date.day, 21, 0, tzinfo=timezone.utc) for r in daily_rows if r.brent_close_usd],
                    [r.brent_close_usd for r in daily_rows if r.brent_close_usd],
                    [None for r in daily_rows if r.brent_close_usd],
                ),
                "CL=F": (
                    [datetime(r.price_date.year, r.price_date.month, r.price_date.day, 21, 0, tzinfo=timezone.utc) for r in daily_rows if r.wti_close_usd],
                    [r.wti_close_usd for r in daily_rows if r.wti_close_usd],
                    [None for r in daily_rows if r.wti_close_usd],
                ),
            }

            impact_rows: list[dict] = []
            for ev in events:
                t0 = ev.market_aligned_at
                for sym in SYMBOLS:
                    times, closes, vols = intraday_series[sym]
                    p0, vol0 = _lookup_price_at(times, closes, vols, t0, max_gap_hours=72.0)
                    use_daily_fallback = False
                    if p0 is None:
                        d_times, d_closes, d_vols = daily_series[sym]
                        p0, vol0 = _lookup_price_at(d_times, d_closes, d_vols, t0, max_gap_hours=96.0)
                        use_daily_fallback = True
                    if p0 is None or p0 <= 0:
                        continue

                    vol_eff = vol0 or DEFAULT_DAILY_VOL
                    row: dict = {
                        "event_id": ev.id,
                        "symbol": sym,
                        "price_t0": round(p0, 4),
                        "vol_20d_t0": round(vol_eff, 6),
                        "ret_pre_24h": None,
                        "ret_pre_4h": None,
                        "ret_1h": None,
                        "ret_4h": None,
                        "ret_24h": None,
                        "ret_3d": None,
                        "ret_7d": None,
                        "ret_14d": None,
                        "z_1h": None,
                        "z_4h": None,
                        "z_24h": None,
                        "z_3d": None,
                        "z_7d": None,
                        "z_14d": None,
                        "max_computed_horizon": None,
                        "updated_at": now,
                    }

                    max_h: Optional[str] = None
                    for h_code, offset_h, ret_col, z_col in HORIZON_SPECS:
                        target_t = t0 + timedelta(hours=offset_h)
                        if offset_h > 0 and target_t > now:
                            continue
                        if use_daily_fallback and abs(offset_h) < 24:
                            continue

                        src_times, src_closes, src_vols = (
                            daily_series[sym] if use_daily_fallback else (times, closes, vols)
                        )
                        max_gap = 12.0 if abs(offset_h) <= 4 else 72.0
                        p_h, _ = _lookup_price_at(src_times, src_closes, src_vols, target_t, max_gap_hours=max_gap)
                        if p_h is None or p_h <= 0:
                            continue

                        # Pre-event drift: log(p0 / p_pre); Post-event reaction: log(p_h / p0)
                        log_ret = math.log(p0 / p_h) if offset_h < 0 else math.log(p_h / p0)
                        row[ret_col] = round(log_ret, 6)
                        if z_col:
                            row[z_col] = compute_z_score(log_ret, vol_eff, offset_h)
                            max_h = h_code

                    row["max_computed_horizon"] = max_h
                    impact_rows.append(row)

            if impact_rows:
                chunk_size = 500
                for i in range(0, len(impact_rows), chunk_size):
                    chunk = impact_rows[i : i + chunk_size]
                    stmt = pg_insert(NewsEventPriceImpact).values(chunk)
                    upsert = stmt.on_conflict_do_update(
                        index_elements=["event_id", "symbol"],
                        set_={
                            col: getattr(stmt.excluded, col)
                            for col in chunk[0].keys()
                            if col not in ("event_id", "symbol")
                        },
                    )
                    await session.execute(upsert)
                await session.commit()

        logger.info(f"NewsImpactAnalyzer: upserted {len(impact_rows)} event-symbol impact rows.")
        return len(impact_rows)

    async def update_impact_priors(self, as_of_date: Optional[date] = None) -> int:
        """Compute walk-forward Empirical Bayes impact priors strictly from matured events."""
        now = datetime.now(timezone.utc)
        target_date = as_of_date or now.date()
        as_of_dt = datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59, tzinfo=timezone.utc)

        async with async_session() as session:
            rows = (
                await session.execute(
                    select(NewsEvent, NewsEventPriceImpact)
                    .join(NewsEventPriceImpact, NewsEventPriceImpact.event_id == NewsEvent.id)
                    .where(NewsEvent.market_aligned_at <= as_of_dt)
                )
            ).all()

            # Group by (event_type, symbol, horizon) enforcing strict non-leakage (t0 + h <= as_of_dt)
            buckets: dict[tuple[str, str, str], tuple[list[float], list[float], list[float]]] = defaultdict(
                lambda: ([], [], [])
            )

            for ev, imp in rows:
                for h_code, offset_h, ret_col, z_col in HORIZON_SPECS:
                    # Ensure outcome window closed before as_of_dt (anti-leakage!)
                    if offset_h > 0 and (ev.market_aligned_at + timedelta(hours=offset_h)) > as_of_dt:
                        continue
                    r_val = getattr(imp, ret_col)
                    if r_val is None:
                        continue
                    z_val = getattr(imp, z_col) if z_col else compute_z_score(r_val, imp.vol_20d_t0 or DEFAULT_DAILY_VOL, abs(offset_h))
                    rets, zs, dirs = buckets[(ev.event_type, imp.symbol, h_code)]
                    rets.append(float(r_val))
                    zs.append(float(z_val))
                    dirs.append(float(ev.effective_direction or 0.0))

            # Compute per-category half-life across post horizons
            stats_by_key: dict[tuple[str, str, str], dict] = {}
            cat_sym_z: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
            for (ev_type, sym, h_code), (rets, zs, dirs) in buckets.items():
                st = compute_empirical_bayes_stats(rets, zs, dirs)
                stats_by_key[(ev_type, sym, h_code)] = st
                if h_code in POST_HORIZONS and st["mean_z"] is not None:
                    cat_sym_z[(ev_type, sym)][h_code] = st["mean_z"]

            prior_rows: list[dict] = []
            for (ev_type, sym, h_code), st in stats_by_key.items():
                default_hl = EVENT_CATEGORIES.get(ev_type, EVENT_CATEGORIES["geopolitical_escalation"]).default_half_life_hours
                hl = estimate_half_life_hours(cat_sym_z.get((ev_type, sym), {}), default_hl)
                prior_rows.append({
                    "as_of_date": target_date,
                    "event_type": ev_type,
                    "region_scope": "global",
                    "symbol": sym,
                    "horizon": h_code,
                    "sample_count": st["sample_count"],
                    "mean_ret": st["mean_ret"],
                    "median_ret": st["median_ret"],
                    "mean_z": st["mean_z"],
                    "hit_rate": st["hit_rate"],
                    "t_stat": st["t_stat"],
                    "shrunk_weight": st["shrunk_weight"],
                    "half_life_hours": hl,
                    "computed_at": now,
                })

            if prior_rows:
                stmt = pg_insert(NewsImpactPrior).values(prior_rows)
                upsert = stmt.on_conflict_do_update(
                    index_elements=["as_of_date", "event_type", "region_scope", "symbol", "horizon"],
                    set_={
                        "sample_count": stmt.excluded.sample_count,
                        "mean_ret": stmt.excluded.mean_ret,
                        "median_ret": stmt.excluded.median_ret,
                        "mean_z": stmt.excluded.mean_z,
                        "hit_rate": stmt.excluded.hit_rate,
                        "t_stat": stmt.excluded.t_stat,
                        "shrunk_weight": stmt.excluded.shrunk_weight,
                        "half_life_hours": stmt.excluded.half_life_hours,
                        "computed_at": stmt.excluded.computed_at,
                    },
                )
                await session.execute(upsert)

                # Update learned_impact_score on recent events using peak Brent shrunk_weight
                peak_weight_by_cat: dict[str, float] = {}
                for pr in prior_rows:
                    if pr["symbol"] == "BZ=F" and pr["horizon"] in POST_HORIZONS:
                        cur = peak_weight_by_cat.get(pr["event_type"], 0.0)
                        if abs(pr["shrunk_weight"]) > abs(cur):
                            peak_weight_by_cat[pr["event_type"]] = pr["shrunk_weight"]

                if peak_weight_by_cat:
                    recent_evs = (
                        await session.execute(
                            select(NewsEvent).where(NewsEvent.first_seen_at >= now - timedelta(days=90))
                        )
                    ).scalars().all()
                    for ev in recent_evs:
                        w = peak_weight_by_cat.get(ev.event_type)
                        if w is not None:
                            ev.learned_impact_score = round((ev.prior_rank_score / 100.0) * abs(w), 4)

                await session.commit()

        logger.info(f"NewsImpactAnalyzer: upserted {len(prior_rows)} impact prior rows for {target_date}.")
        return len(prior_rows)

    async def backfill_walkforward_priors(self, days: int = 90) -> dict[str, int]:
        """Recompute walk-forward Empirical Bayes priors for every date with events in the window.

        Ensures historical ML snapshots can use strictly out-of-sample priors (as_of_date <= d).
        """
        now = datetime.now(timezone.utc)
        today = now.date()
        cutoff = today - timedelta(days=days)

        async with async_session() as session:
            ev_dates = (
                await session.execute(
                    select(NewsEvent.market_aligned_at).where(
                        NewsEvent.market_aligned_at >= datetime(cutoff.year, cutoff.month, cutoff.day, tzinfo=timezone.utc)
                    )
                )
            ).scalars().all()

        unique_dates = sorted({dt.date() for dt in ev_dates if dt is not None} | {today})
        total_rows = 0
        for d in unique_dates:
            total_rows += await self.update_impact_priors(as_of_date=d)

        return {"dates_computed": len(unique_dates), "prior_rows_upserted": total_rows}

