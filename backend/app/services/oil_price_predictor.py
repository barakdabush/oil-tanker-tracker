"""Unified Bottom-Line Oil Price Predictor.

Synthesizes all data streams in oil-tanker-tracker into a single actionable
Brent (BZ=F) / WTI (CL=F) price forecast across 24h, 3d, and 7d horizons:

1. News Analog Memory Signal (35%): active 48h events matched to historical
   precedents via similarity search + Empirical Bayes priors.
2. Chokepoint & Tanker Flow Signal (30%): Hormuz / Bab el-Mandeb / Cape rerouting
   transits, PortWatch tanker volumes, and Straits.live crisis pressure.
3. Dark Fleet & Supply Chain Signal (20%): AIS gaps, STS transfers, and
   floating storage / idle tanker build-up.
4. Market Momentum & Regime Signal (15%): trailing 3d/7d price momentum and
   realized volatility.

Blends the transparent 4-pillar expected move with a strictly out-of-sample
walk-forward L2-regularized (Ridge) regression trained on ``oil_market_snapshots``
using pure ``numpy`` (zero heavy ML dependencies, zero future leakage).
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import numpy as np
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chokepoint import PortWatchChokepointDaily
from app.models.global_oil_features import (
    OilFundamentalsWeekly,
    OilMacroDaily,
    OilMarketSnapshot,
    OilPrice,
    StraitRiskDaily,
)
from app.models.news import AIMarketBriefing
from app.schemas.forecast import (
    BottomLineForecastResponse,
    ForecastDriver,
    ForecastPillar,
    HorizonForecast,
)
from app.services.gbt_oil_model import (
    compute_oos_feature_importance,
    run_har_volatility_ensemble,
    run_walkforward_regularized_wave,
    run_walkforward_spike_detector,
    run_walkforward_volatility_alert,
    run_walkforward_wave_classifier,
)
from app.services.news_predictor import build_news_analog_memory

PILLAR_WEIGHTS = {
    "news_analogs": 0.35,
    "chokepoint_flow": 0.30,
    "dark_fleet_supply": 0.20,
    "market_momentum": 0.15,
}

# Features used in walk-forward Ridge regression on oil_market_snapshots
RIDGE_FEATURE_KEYS: tuple[str, ...] = (
    "strait_of_hormuz_transits",
    "cape_of_good_hope_transits",
    "bab_el_mandeb_transits",
    "dark_vessels_count",
    "new_ais_gaps_24h",
    "sts_events_24h",
    "vessels_idle_gt_48h",
    "floating_storage_count",
    "hormuz_crisis_pressure",
    "news_net_impact_1d",
    "news_net_impact_7d",
    "news_supply_risk_score",
    "news_sanctions_pressure_7d",
)

FORECAST_HORIZONS: tuple[tuple[str, int], ...] = (
    ("3d", 3),
    ("5d", 5),
    ("7d", 7),
)

DEFAULT_DAILY_VOL_PCT = 2.0


def _clamp(val: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, val))


def _direction_from_score(score: float, threshold: float = 12.0) -> str:
    if score >= threshold:
        return "bullish"
    if score <= -threshold:
        return "bearish"
    return "neutral"


def _verdict_from_return(ret_pct: float, horizon_days: int) -> tuple[str, str]:
    scale = math.sqrt(max(1, horizon_days))
    strong_th = 1.6 * scale
    mild_th = 0.45 * scale
    if ret_pct >= strong_th:
        return "Strong Bullish", "bullish"
    if ret_pct >= mild_th:
        return "Bullish", "bullish"
    if ret_pct <= -strong_th:
        return "Strong Bearish", "bearish"
    if ret_pct <= -mild_th:
        return "Bearish", "bearish"
    return "Neutral", "neutral"


def _trailing_z(values: list[float], latest: Optional[float]) -> Optional[float]:
    clean = [v for v in values if v is not None and math.isfinite(v)]
    if latest is None or not math.isfinite(latest) or len(clean) < 4:
        return None
    mean = sum(clean) / len(clean)
    var = sum((v - mean) ** 2 for v in clean) / len(clean)
    std = math.sqrt(var)
    if std <= 1e-6:
        return 0.0
    return _clamp((latest - mean) / std, -3.0, 3.0)


def compute_realized_daily_vol_pct(prices: list[float]) -> float:
    """Compute trailing 20-day realized daily volatility in percent."""
    clean = [p for p in prices if p is not None and p > 0]
    if len(clean) < 5:
        return DEFAULT_DAILY_VOL_PCT
    recent = clean[-21:]
    rets = [math.log(recent[i] / recent[i - 1]) for i in range(1, len(recent)) if recent[i - 1] > 0]
    if len(rets) < 3:
        return DEFAULT_DAILY_VOL_PCT
    std = float(np.std(rets, ddof=1)) * 100.0
    return round(_clamp(std, 0.75, 6.5), 2)


def run_walkforward_ridge(
    snapshot_dicts: list[dict[str, Any]],
    price_key: str,
    horizon_days: int,
    ridge_alpha: float = 12.0,
    min_train_rows: int = 14,
) -> tuple[Optional[float], Optional[float], Optional[float], int]:
    """Expanding-window walk-forward Ridge regression on daily snapshots.

    Returns ``(latest_pred_pct, backtest_hit_rate_pct, backtest_mae_pct, backtest_samples)``.
    Strictly anti-leakage: at step ``t``, only rows ``<= t - horizon_days`` are used
    for fitting and feature standardization.
    """
    # Keep chronologically ordered rows that have a valid price
    priced_rows = [
        r for r in snapshot_dicts
        if r.get(price_key) is not None and float(r[price_key]) > 0
    ]
    n = len(priced_rows)
    if n < min_train_rows + horizon_days + 2:
        return None, None, None, 0

    # Build raw feature matrix (n x F) and forward log-return targets
    feat_cols: list[str] = []
    for k in RIDGE_FEATURE_KEYS:
        non_null = sum(1 for r in priced_rows if r.get(k) is not None)
        if non_null >= max(6, n // 3):
            feat_cols.append(k)
    # Always include 3d price momentum as an autoregressive feature
    n_feats = len(feat_cols) + 1

    X_raw = np.zeros((n, n_feats), dtype=np.float64)
    prices = np.array([float(r[price_key]) for r in priced_rows], dtype=np.float64)

    for i, r in enumerate(priced_rows):
        for j, k in enumerate(feat_cols):
            val = r.get(k)
            X_raw[i, j] = float(val) if val is not None else np.nan
        # Trailing 3-day momentum
        if i >= 3 and prices[i - 3] > 0:
            X_raw[i, -1] = math.log(prices[i] / prices[i - 3])
        else:
            X_raw[i, -1] = 0.0

    y_fwd = np.full(n, np.nan, dtype=np.float64)
    for i in range(n - horizon_days):
        if prices[i] > 0 and prices[i + horizon_days] > 0:
            y_fwd[i] = math.log(prices[i + horizon_days] / prices[i])

    def _fit_and_predict(train_end_excl: int, test_idx: int) -> Optional[float]:
        X_tr = X_raw[:train_end_excl].copy()
        y_tr = y_fwd[:train_end_excl]
        valid_mask = np.isfinite(y_tr)
        if int(valid_mask.sum()) < min_train_rows:
            return None
        X_tr = X_tr[valid_mask]
        y_tr = y_tr[valid_mask]

        # Impute NaNs using training column medians, then z-score
        col_means = np.nanmean(X_tr, axis=0)
        col_means = np.where(np.isfinite(col_means), col_means, 0.0)
        inds = np.where(~np.isfinite(X_tr))
        X_tr[inds] = np.take(col_means, inds[1])

        col_stds = np.std(X_tr, axis=0)
        col_stds = np.where(col_stds > 1e-6, col_stds, 1.0)
        X_tr_z = np.clip((X_tr - col_means) / col_stds, -3.5, 3.5)

        # Add intercept column (unpenalized)
        ones = np.ones((X_tr_z.shape[0], 1), dtype=np.float64)
        A = np.hstack([ones, X_tr_z])
        reg = np.eye(A.shape[1], dtype=np.float64) * ridge_alpha
        reg[0, 0] = 1e-4

        try:
            w = np.linalg.solve(A.T @ A + reg, A.T @ y_tr)
        except np.linalg.LinAlgError:
            return None

        x_te = X_raw[test_idx].copy()
        bad = ~np.isfinite(x_te)
        x_te[bad] = col_means[bad]
        x_te_z = np.clip((x_te - col_means) / col_stds, -3.5, 3.5)
        pred_log = float(w[0] + np.dot(w[1:], x_te_z))
        pred_pct = (math.exp(_clamp(pred_log, -0.20, 0.20)) - 1.0) * 100.0
        return pred_pct

    # Walk-forward out-of-sample evaluation
    hits = 0
    abs_errs: list[float] = []
    start_eval = min_train_rows + horizon_days
    for t in range(start_eval, n - horizon_days):
        if not math.isfinite(y_fwd[t]):
            continue
        # Only rows whose target matured by time t (i + horizon_days <= t)
        train_end = t - horizon_days + 1
        pred_pct = _fit_and_predict(train_end, t)
        if pred_pct is None:
            continue
        actual_pct = (math.exp(float(y_fwd[t])) - 1.0) * 100.0
        if (pred_pct >= 0 and actual_pct >= 0) or (pred_pct < 0 and actual_pct < 0):
            hits += 1
        abs_errs.append(abs(pred_pct - actual_pct))

    # Latest live prediction using all matured rows
    latest_pred = _fit_and_predict(n - horizon_days, n - 1)
    samples = len(abs_errs)
    if samples == 0:
        return (round(latest_pred, 2) if latest_pred is not None else None), None, None, 0

    hit_rate = round((hits / samples) * 100.0, 1)
    mae = round(sum(abs_errs) / samples, 2)
    return (
        round(latest_pred, 2) if latest_pred is not None else None,
        hit_rate,
        mae,
        samples,
    )


async def build_bottom_line_forecast(
    db: AsyncSession,
    symbol: str = "BZ=F",
) -> BottomLineForecastResponse:
    """Build the unified Bottom-Line Oil Price Forecast for Brent (BZ=F) or WTI (CL=F)."""
    now = datetime.now(timezone.utc)
    sym_label = "Brent Crude" if symbol == "BZ=F" else "WTI Crude"
    price_key = "brent_close_usd" if symbol == "BZ=F" else "wti_close_usd"

    # 1. Load daily snapshots (365d) + 5-year OilPrice & OilMacroDaily + StraitRiskDaily
    cutoff_date = (now - timedelta(days=365)).date()
    cutoff_5y = (now - timedelta(days=1850)).date()
    snaps = list(
        (
            await db.execute(
                select(OilMarketSnapshot)
                .where(OilMarketSnapshot.snapshot_date >= cutoff_date)
                .order_by(OilMarketSnapshot.snapshot_date.asc())
            )
        ).scalars().all()
    )
    price_rows = list(
        (
            await db.execute(
                select(OilPrice)
                .where(OilPrice.price_date >= cutoff_5y)
                .order_by(OilPrice.price_date.asc())
            )
        ).scalars().all()
    )
    macro_rows = list(
        (
            await db.execute(
                select(OilMacroDaily)
                .where(OilMacroDaily.date >= cutoff_5y)
                .order_by(OilMacroDaily.date.asc())
            )
        ).scalars().all()
    )
    fundamentals_rows = list(
        (
            await db.execute(
                select(OilFundamentalsWeekly)
                .where(OilFundamentalsWeekly.report_date >= cutoff_5y)
                .order_by(OilFundamentalsWeekly.report_date.asc())
            )
        ).scalars().all()
    )
    pw_rows = list(
        (
            await db.execute(
                select(
                    PortWatchChokepointDaily.date,
                    PortWatchChokepointDaily.portname,
                    PortWatchChokepointDaily.n_tanker,
                    PortWatchChokepointDaily.capacity_tanker,
                )
                .where(PortWatchChokepointDaily.date >= cutoff_5y)
            )
        ).all()
    )
    pw_by_date: dict = {}
    for pw_d, pname, n_t, cap_t in pw_rows:
        bucket = pw_by_date.setdefault(
            pw_d,
            {
                "pw_hormuz_tankers": 0.0,
                "pw_hormuz_capacity": 0.0,
                "pw_bab_tankers": 0.0,
                "pw_suez_tankers": 0.0,
                "pw_cape_tankers": 0.0,
                "pw_global_tankers": 0.0,
            },
        )
        nt_val = float(n_t or 0.0)
        cap_val = float(cap_t or 0.0)
        bucket["pw_global_tankers"] += nt_val
        p_low = (pname or "").lower()
        if "hormuz" in p_low:
            bucket["pw_hormuz_tankers"] += nt_val
            bucket["pw_hormuz_capacity"] += cap_val
        elif "mandeb" in p_low:
            bucket["pw_bab_tankers"] += nt_val
        elif "suez" in p_low:
            bucket["pw_suez_tankers"] += nt_val
        elif "good hope" in p_low or "cape" in p_low:
            bucket["pw_cape_tankers"] += nt_val

    risk_rows = list(
        (
            await db.execute(
                select(StraitRiskDaily)
                .where(StraitRiskDaily.date >= cutoff_date)
                .order_by(StraitRiskDaily.date.asc())
            )
        ).scalars().all()
    )
    latest_briefing = (
        await db.execute(select(AIMarketBriefing).order_by(desc(AIMarketBriefing.created_at)).limit(1))
    ).scalar_one_or_none()

    price_by_date = {
        p.price_date: (p.brent_close_usd, p.wti_close_usd)
        for p in price_rows
    }
    macro_by_date = {m.date: m for m in macro_rows}
    risk_by_date = {r.date: r for r in risk_rows}

    snapshot_dicts: list[dict[str, Any]] = []
    for s in snaps:
        brent_p, wti_p = price_by_date.get(s.snapshot_date, (None, None))
        rk = risk_by_date.get(s.snapshot_date)
        snapshot_dicts.append({
            "snapshot_date": s.snapshot_date,
            "brent_close_usd": brent_p,
            "wti_close_usd": wti_p,
            "total_active_vessels": s.total_active_vessels,
            "strait_of_hormuz_transits": s.strait_of_hormuz_transits,
            "cape_of_good_hope_transits": s.cape_of_good_hope_transits,
            "bab_el_mandeb_transits": s.bab_el_mandeb_transits,
            "suez_canal_transits": s.suez_canal_transits,
            "dark_vessels_count": s.dark_vessels_count,
            "new_ais_gaps_24h": s.new_ais_gaps_24h,
            "sts_events_24h": s.sts_events_24h,
            "vessels_idle_gt_48h": s.vessels_idle_gt_48h,
            "floating_storage_count": s.floating_storage_count,
            "hormuz_crisis_pressure": rk.hormuz_crisis_pressure if rk else None,
            "hormuz_escalation_prob": rk.hormuz_escalation_prob if rk else None,
            "hormuz_stranded_vessels": rk.hormuz_stranded_vessels if rk else None,
            "news_net_impact_1d": s.news_net_impact_1d,
            "news_net_impact_7d": s.news_net_impact_7d,
            "news_supply_risk_score": s.news_supply_risk_score,
            "news_sanctions_pressure_7d": s.news_sanctions_pressure_7d,
        })

    # Latest spot/close price & trailing realized volatility
    all_prices = [
        float(getattr(p, price_key))
        for p in price_rows
        if getattr(p, price_key, None) is not None and float(getattr(p, price_key)) > 0
    ]
    latest_price_row = next(
        (p for p in reversed(price_rows) if getattr(p, price_key, None) is not None),
        None,
    )
    current_price = (
        round(float(getattr(latest_price_row, price_key)), 2)
        if latest_price_row is not None
        else None
    )
    price_date = latest_price_row.price_date if latest_price_row is not None else None
    daily_vol_pct = compute_realized_daily_vol_pct(all_prices)

    # 2. Pillar 1 — News Analog Memory (active 48h events)
    memory = await build_news_analog_memory(db, symbol=symbol)
    cutoff_48h = now - timedelta(hours=48)
    active_events = [r for r in memory.records if r.first_seen_at >= cutoff_48h]
    active_events.sort(key=lambda r: r.prior_rank_score, reverse=True)

    news_horizon_weighted: dict[str, list[tuple[float, float]]] = {"24h": [], "3d": [], "7d": []}
    news_drivers: list[ForecastDriver] = []

    for rec in active_events[:25]:
        pred = memory.predict_for_event(rec, symbol=symbol, reference_time=now)
        age_hours = max(0.0, (now - rec.first_seen_at).total_seconds() / 3600.0)
        decay = math.exp(-age_hours / 36.0)
        rank_w = max(0.2, rec.prior_rank_score / 100.0)
        conf_w = max(0.15, pred.confidence_pct / 100.0)
        w = decay * rank_w * conf_w

        for h_key in ("24h", "3d", "7d"):
            hp = pred.horizons.get(h_key)
            if hp is not None:
                news_horizon_weighted[h_key].append((w, hp.expected_return_pct))

        if len(news_drivers) < 3 and abs(pred.expected_return_pct) >= 0.15:
            news_drivers.append(
                ForecastDriver(
                    source_type="news",
                    title=rec.headline[:140],
                    impact_direction=pred.direction,
                    expected_move_pct=pred.expected_return_pct,
                    horizon=pred.peak_horizon,
                    confidence_pct=pred.confidence_pct,
                    detail=pred.narrative,
                )
            )

    def _agg_news_ret(h_key: str) -> float:
        pairs = news_horizon_weighted.get(h_key, [])
        if not pairs:
            return 0.0
        w_sum = sum(w for w, _ in pairs)
        if w_sum <= 1e-6:
            return 0.0
        # Dampen extreme noise while preserving direction & magnitude
        avg = sum(w * r for w, r in pairs) / w_sum
        return _clamp(avg, -6.0, 6.0)

    news_ret_24h = _agg_news_ret("24h")
    news_ret_3d = _agg_news_ret("3d")
    news_ret_7d = _agg_news_ret("7d")
    news_score = round(_clamp((news_ret_24h / max(1.0, daily_vol_pct)) * 55.0, -100.0, 100.0), 1)
    news_metrics = [
        f"{len(active_events)} active events (48h) matched against {len(memory.records)} historical events",
        f"Analog-implied move: {news_ret_24h:+.2f}% (24h) · {news_ret_3d:+.2f}% (3d) · {news_ret_7d:+.2f}% (7d)",
    ]
    news_pillar = ForecastPillar(
        key="news_analogs",
        label="News Analog Memory",
        score=news_score,
        weight=PILLAR_WEIGHTS["news_analogs"],
        direction=_direction_from_score(news_score),
        summary=(
            f"Active headlines match {len(memory.records)} historical precedents, "
            f"implying {news_ret_24h:+.2f}% (24h) and {news_ret_3d:+.2f}% (3d) for {sym_label}."
        ),
        metrics=news_metrics,
    )

    # 3. Pillar 2 — Chokepoint & Tanker Flow
    recent_30 = snapshot_dicts[-30:]
    latest_snap = snapshot_dicts[-1] if snapshot_dicts else {}
    latest_risk = risk_rows[-1] if risk_rows else None

    cp_signals: list[float] = []
    cp_metrics: list[str] = []
    physical_drivers: list[ForecastDriver] = []

    hormuz_hist = [r["strait_of_hormuz_transits"] for r in recent_30[:-1] if r.get("strait_of_hormuz_transits") is not None]
    hormuz_now = latest_snap.get("strait_of_hormuz_transits")
    z_hormuz = _trailing_z(hormuz_hist, hormuz_now)
    if z_hormuz is not None:
        # Lower Hormuz transits = higher supply disruption risk (bullish)
        cp_signals.append(-z_hormuz * 40.0)
        avg_h = sum(hormuz_hist) / len(hormuz_hist)
        cp_metrics.append(f"Hormuz transits: {hormuz_now} vs {avg_h:.1f} 30d avg")
        if z_hormuz <= -0.75:
            physical_drivers.append(
                ForecastDriver(
                    source_type="chokepoint",
                    title=f"Strait of Hormuz transit drop ({hormuz_now} vs {avg_h:.1f} avg)",
                    impact_direction="bullish",
                    detail="Reduced tanker flow through Hormuz tightens near-term seaborne crude supply.",
                )
            )

    cape_hist = [r["cape_of_good_hope_transits"] for r in recent_30[:-1] if r.get("cape_of_good_hope_transits") is not None]
    cape_now = latest_snap.get("cape_of_good_hope_transits")
    z_cape = _trailing_z(cape_hist, cape_now)
    if z_cape is not None:
        # Surge around Cape of Good Hope = rerouting / longer ton-miles (bullish)
        cp_signals.append(z_cape * 25.0)
        cp_metrics.append(f"Cape rerouting transits: {cape_now} (z={z_cape:+.1f})")

    if latest_risk and latest_risk.hormuz_crisis_pressure is not None:
        # Pressure index 0..100; >60 signals elevated geopolitical risk premium
        pressure = float(latest_risk.hormuz_crisis_pressure)
        cp_signals.append(_clamp((pressure - 50.0) * 1.4, -50.0, 75.0))
        cp_metrics.append(f"Hormuz Crisis Pressure: {pressure:.0f}/100")
        if pressure >= 70.0:
            physical_drivers.append(
                ForecastDriver(
                    source_type="chokepoint",
                    title=f"Elevated Hormuz Crisis Pressure ({pressure:.0f}/100)",
                    impact_direction="bullish",
                    detail="Straits.live maritime risk index signals heightened chokepoint disruption risk.",
                )
            )

    cp_score = round(_clamp(sum(cp_signals) / max(1, len(cp_signals)), -100.0, 100.0), 1)
    cp_pillar = ForecastPillar(
        key="chokepoint_flow",
        label="Chokepoints & Tanker Flows",
        score=cp_score,
        weight=PILLAR_WEIGHTS["chokepoint_flow"],
        direction=_direction_from_score(cp_score),
        summary=(
            "Chokepoint traffic & maritime risk indices point to elevated supply friction."
            if cp_score >= 15
            else "Chokepoint transits are flowing normally without major disruption pressure."
            if cp_score > -15
            else "Chokepoint flows are recovering, easing supply disruption risk."
        ),
        metrics=cp_metrics or ["Awaiting chokepoint transit baseline"],
    )

    # 4. Pillar 3 — Dark Fleet & Supply Chain
    df_signals: list[float] = []
    df_metrics: list[str] = []

    dark_hist = [r["dark_vessels_count"] for r in recent_30[:-1] if r.get("dark_vessels_count") is not None]
    dark_now = latest_snap.get("dark_vessels_count")
    z_dark = _trailing_z(dark_hist, dark_now)
    if z_dark is not None:
        df_signals.append(z_dark * 35.0)
        avg_d = sum(dark_hist) / len(dark_hist)
        df_metrics.append(f"Dark fleet: {dark_now} vs {avg_d:.0f} 30d avg")
        if z_dark >= 0.9:
            physical_drivers.append(
                ForecastDriver(
                    source_type="dark_fleet",
                    title=f"Dark fleet surge ({dark_now} tankers vs {avg_d:.0f} avg)",
                    impact_direction="bullish",
                    detail="Spike in AIS-dark tankers signals heightened sanctions friction and covert routing.",
                )
            )

    sts_hist = [r["sts_events_24h"] for r in recent_30[:-1] if r.get("sts_events_24h") is not None]
    sts_now = latest_snap.get("sts_events_24h")
    z_sts = _trailing_z(sts_hist, sts_now)
    if z_sts is not None:
        df_signals.append(z_sts * 25.0)
        df_metrics.append(f"STS transfers (24h): {sts_now} (z={z_sts:+.1f})")

    float_hist = [r["floating_storage_count"] for r in recent_30[:-1] if r.get("floating_storage_count") is not None]
    float_now = latest_snap.get("floating_storage_count")
    z_float = _trailing_z(float_hist, float_now)
    if z_float is not None:
        # Rising floating storage = unsold crude glut (bearish)
        df_signals.append(-z_float * 30.0)
        df_metrics.append(f"Floating storage tankers: {float_now} (z={z_float:+.1f})")

    df_score = round(_clamp(sum(df_signals) / max(1, len(df_signals)), -100.0, 100.0), 1)
    df_pillar = ForecastPillar(
        key="dark_fleet_supply",
        label="Dark Fleet & Supply Chain",
        score=df_score,
        weight=PILLAR_WEIGHTS["dark_fleet_supply"],
        direction=_direction_from_score(df_score),
        summary=(
            "Dark-fleet & STS activity is elevated above trailing norms, signaling sanctions/supply tightness."
            if df_score >= 15
            else "Supply-chain & shadow-fleet indicators are near normal levels."
            if df_score > -15
            else "Floating storage build / lower dark-fleet stress leans bearish."
        ),
        metrics=df_metrics or ["Awaiting dark-fleet baseline"],
    )

    # 5. Pillar 4 — Macro, Crack Spread & Price Momentum
    mom_3d_pct = (
        ((all_prices[-1] - all_prices[-4]) / all_prices[-4]) * 100.0
        if len(all_prices) >= 4
        else 0.0
    )
    mom_7d_pct = (
        ((all_prices[-1] - all_prices[-8]) / all_prices[-8]) * 100.0
        if len(all_prices) >= 8
        else mom_3d_pct
    )
    mom_signals: list[float] = [_clamp((0.6 * mom_3d_pct + 0.4 * mom_7d_pct) * 14.0, -100.0, 100.0)]
    mom_metrics = [
        f"3d trend: {mom_3d_pct:+.2f}% · 7d trend: {mom_7d_pct:+.2f}% · 20d vol: {daily_vol_pct:.2f}%",
    ]

    latest_macro = macro_rows[-1] if macro_rows else None
    prev_macro_5d = macro_rows[-6] if len(macro_rows) >= 6 else None
    if latest_macro and latest_macro.crack_spread_321_usd is not None:
        crack_now = float(latest_macro.crack_spread_321_usd)
        crack_5d_chg = (
            round(crack_now - float(prev_macro_5d.crack_spread_321_usd), 2)
            if (prev_macro_5d and prev_macro_5d.crack_spread_321_usd is not None)
            else 0.0
        )
        # Rising 3-2-1 crack spread = stronger refinery demand for crude (bullish)
        mom_signals.append(_clamp(crack_5d_chg * 14.0, -70.0, 70.0))
        mom_metrics.append(f"3-2-1 Crack Spread: ${crack_now:.2f}/bbl ({crack_5d_chg:+.2f} 5d)")
        if abs(crack_5d_chg) >= 1.5:
            physical_drivers.append(
                ForecastDriver(
                    source_type="market",
                    title=f"3-2-1 Refinery Crack Spread ${crack_now:.2f}/bbl ({crack_5d_chg:+.2f} 5d)",
                    impact_direction="bullish" if crack_5d_chg > 0 else "bearish",
                    detail=(
                        "Expanding refining margins incentivize higher crude runs."
                        if crack_5d_chg > 0
                        else "Compressing refining margins signal softer fuel demand."
                    ),
                )
            )
    if latest_macro and latest_macro.tanker_etf_close is not None and prev_macro_5d and prev_macro_5d.tanker_etf_close:
        fro_5d = ((latest_macro.tanker_etf_close - prev_macro_5d.tanker_etf_close) / prev_macro_5d.tanker_etf_close) * 100.0
        mom_signals.append(_clamp(fro_5d * 8.0, -50.0, 50.0))
        mom_metrics.append(f"Tanker Equity (FRO): {fro_5d:+.1f}% 5d · VIX: {latest_macro.vix_close or '—'}")
    if (
        latest_macro
        and latest_macro.curve_backwardation_proxy is not None
        and prev_macro_5d
        and prev_macro_5d.curve_backwardation_proxy
    ):
        curve_5d = ((latest_macro.curve_backwardation_proxy / prev_macro_5d.curve_backwardation_proxy) - 1.0) * 100.0
        mom_signals.append(_clamp(curve_5d * 18.0, -55.0, 55.0))
        mom_metrics.append(
            f"Curve (USO/USL): {curve_5d:+.2f}% 5d ({'Backwardation' if curve_5d > 0 else 'Contango'} shift)"
        )
    if latest_macro and latest_macro.gulf_hurricane_risk and latest_macro.gulf_hurricane_risk >= 25.0:
        hr = float(latest_macro.gulf_hurricane_risk)
        mom_signals.append(_clamp(hr * 0.65, 0.0, 65.0))
        mom_metrics.append(f"NOAA Gulf Hurricane Risk: {hr:.0f}/100")
        physical_drivers.insert(
            0,
            ForecastDriver(
                source_type="chokepoint",
                title=f"NOAA Gulf of Mexico Hurricane Threat ({hr:.0f}/100)",
                impact_direction="bullish",
                detail="Active tropical system threatening US Gulf offshore crude production & refining.",
            ),
        )

    # Latest weekly CFTC Managed Money & EIA storage signals
    latest_cftc = next((f for f in reversed(fundamentals_rows) if f.cftc_mm_net_pct_oi is not None), None)
    if latest_cftc is not None and latest_cftc.cftc_mm_net_pct_oi is not None:
        mom_metrics.append(
            f"CFTC Hedge-Fund Net: {latest_cftc.cftc_mm_net_pct_oi:+.1f}% OI ({latest_cftc.report_date.isoformat()})"
        )
    latest_eia = next((f for f in reversed(fundamentals_rows) if f.eia_crude_stocks_chg_kbbl is not None), None)
    if latest_eia is not None and latest_eia.eia_crude_stocks_chg_kbbl is not None:
        eia_chg_mbbl = latest_eia.eia_crude_stocks_chg_kbbl / 1000.0
        # Draw (negative change) = bullish; Build (positive change) = bearish
        mom_signals.append(_clamp(-eia_chg_mbbl * 12.0, -55.0, 55.0))
        mom_metrics.append(f"EIA Crude Storage: {eia_chg_mbbl:+.2f}M bbl WoW")
        if abs(eia_chg_mbbl) >= 2.0:
            physical_drivers.append(
                ForecastDriver(
                    source_type="market",
                    title=f"EIA US Crude Storage {'Draw' if eia_chg_mbbl < 0 else 'Build'} ({eia_chg_mbbl:+.2f}M bbl)",
                    impact_direction="bullish" if eia_chg_mbbl < 0 else "bearish",
                    detail=f"Weekly physical inventory shift as of {latest_eia.report_date.isoformat()}.",
                )
            )

    mom_score = round(_clamp(sum(mom_signals) / max(1, len(mom_signals)), -100.0, 100.0), 1)
    if abs(mom_7d_pct) >= 2.5:
        physical_drivers.append(
            ForecastDriver(
                source_type="market",
                title=f"{sym_label} 7d momentum ({mom_7d_pct:+.2f}%)",
                impact_direction="bullish" if mom_7d_pct > 0 else "bearish",
                expected_move_pct=round(mom_3d_pct * 0.35, 2),
                horizon="3d",
                detail=f"Recent futures trend ({mom_3d_pct:+.2f}% over 3d) with {daily_vol_pct:.2f}% daily volatility.",
            )
        )

    mom_pillar = ForecastPillar(
        key="market_momentum",
        label="Macro, Storage, Curve & Margins",
        score=mom_score,
        weight=PILLAR_WEIGHTS["market_momentum"],
        direction=_direction_from_score(mom_score),
        summary=(
            f"Refining margins, curve & positioning lean bullish ({mom_7d_pct:+.2f}% 7d)."
            if mom_score >= 15
            else f"Macro, storage & refining indicators are balanced ({mom_7d_pct:+.2f}% 7d)."
            if mom_score > -15
            else f"Downward momentum / softer margins weigh on {sym_label} ({mom_7d_pct:+.2f}% 7d)."
        ),
        metrics=mom_metrics,
    )

    pillars = [news_pillar, cp_pillar, df_pillar, mom_pillar]
    composite_score = round(sum(p.score * p.weight for p in pillars), 1)

    # Physical Ocean vs. Wall Street Market Divergence check
    physical_ocean_score = round((cp_pillar.score + df_pillar.score) / 2.0, 1)
    if abs(physical_ocean_score - mom_pillar.score) >= 35.0:
        if physical_ocean_score > mom_pillar.score:
            physical_drivers.insert(
                0,
                ForecastDriver(
                    source_type="chokepoint",
                    title=f"Physical vs. Market Divergence (Ocean {physical_ocean_score:+.0f} vs Market {mom_pillar.score:+.0f})",
                    impact_direction="bullish",
                    detail="Physical tanker flows & dark-fleet stress are tighter than recent futures price action implies.",
                ),
            )
        else:
            physical_drivers.insert(
                0,
                ForecastDriver(
                    source_type="chokepoint",
                    title=f"Physical vs. Market Divergence (Ocean {physical_ocean_score:+.0f} vs Market {mom_pillar.score:+.0f})",
                    impact_direction="bearish",
                    detail="Physical seaborne flows look looser than futures momentum implies.",
                ),
            )

    # 6. Build 5-year daily feature table (Prices + Macro/Demand/Curve + CFTC/EIA + 5y PortWatch + 5y News)
    gbt_daily_rows = _assemble_gbt_daily_rows(
        price_rows=price_rows,
        macro_by_date=macro_by_date,
        pw_by_date=pw_by_date,
        fundamentals_rows=fundamentals_rows,
        news_records=memory.records,
    )

    # 7. Multi-day Wave Forecasts (3d / 5d / 7d) blending Pillars + News Analogs + 10-Factor Regularized Wave
    news_ret_map = {"3d": news_ret_3d, "5d": (news_ret_3d + news_ret_7d) / 2.0, "7d": news_ret_7d}
    analog_bt = memory.evaluate_walkforward_accuracy(symbol=symbol, sample_limit=160)
    horizons_out: dict[str, HorizonForecast] = {}

    for h_label, h_days in FORECAST_HORIZONS:
        h_scale = math.sqrt(h_days)
        non_news_score = (
            cp_pillar.score * cp_pillar.weight
            + df_pillar.score * df_pillar.weight
            + mom_pillar.score * mom_pillar.weight
        ) / (1.0 - news_pillar.weight)
        non_news_ret = (non_news_score / 100.0) * daily_vol_pct * h_scale * 0.85
        pillar_ret = round(
            news_pillar.weight * news_ret_map[h_label] + (1.0 - news_pillar.weight) * non_news_ret,
            2,
        )

        wave_res = run_walkforward_regularized_wave(
            daily_rows=gbt_daily_rows,
            price_key=price_key,
            horizon_days=h_days,
        )

        # Strict statistical gating: blend only when non-overlapping independent waves show real edge (p <= 0.15)
        wave_has_edge = (
            wave_res.expected_wave_pct is not None
            and wave_res.indep_samples >= 15
            and wave_res.indep_hit_rate_pct is not None
            and wave_res.indep_hit_rate_pct >= 53.0
            and wave_res.indep_p_value is not None
            and wave_res.indep_p_value <= 0.15
        )

        if wave_has_edge and wave_res.expected_wave_pct is not None:
            ml_pred = wave_res.expected_wave_pct
            edge_pts = max(0.0, (wave_res.indep_hit_rate_pct or 50.0) - 50.0)
            ml_weight = round(_clamp(0.35 + edge_pts * 0.03, 0.35, 0.65), 2)
            blended_ret = round((1.0 - ml_weight) * pillar_ret + ml_weight * ml_pred, 2)
            hit_rate = wave_res.signal_hit_rate_pct
            mae = wave_res.mae_pct
            bt_samples = wave_res.signal_samples
            hc_hit = wave_res.high_conviction_hit_rate_pct
            hc_samples = wave_res.high_conviction_samples
            model_name = "10-Factor Regularized Wave + 4-Pillar"
        else:
            ml_pred = wave_res.expected_wave_pct
            ml_weight = 0.0
            blended_ret = pillar_ret
            hc_hit = wave_res.high_conviction_hit_rate_pct
            hc_samples = wave_res.high_conviction_samples
            if wave_res.signal_samples >= 20 and wave_res.signal_hit_rate_pct is not None:
                hit_rate, mae, bt_samples = wave_res.signal_hit_rate_pct, wave_res.mae_pct, wave_res.signal_samples
                model_name = "4-Pillar + News Analogs (ML Gated: p > 0.15)"
            else:
                abt = analog_bt.get("3d" if h_label == "5d" else h_label)
                if abt:
                    hit_rate, mae, bt_samples = abt["hit_rate_pct"], abt["mae_pct"], abt["samples"]
                else:
                    hit_rate, mae, bt_samples = None, None, 0
                model_name = "4-Pillar + News Analogs"

        blended_ret = round(_clamp(blended_ret, -12.0, 12.0), 2)
        verdict, direction = _verdict_from_return(blended_ret, h_days)

        wave_state = wave_res.wave_state
        if wave_state == "Range-Bound" and abs(blended_ret) >= (1.1 * math.sqrt(h_days / 3.0)):
            wave_state = "Bullish Wave" if blended_ret > 0 else "Bearish Wave"

        signs = [1 if p.score > 10 else (-1 if p.score < -10 else 0) for p in pillars]
        non_zero_signs = [s for s in signs if s != 0]
        target_sign = 1 if blended_ret >= 0 else -1
        agreement = (
            sum(1 for s in non_zero_signs if s == target_sign) / len(non_zero_signs)
            if non_zero_signs
            else 0.5
        )
        strength = min(1.0, abs(composite_score) / 65.0)
        bt_bonus = ((hit_rate - 50.0) / 50.0) * 18.0 if (hit_rate is not None and bt_samples >= 20) else 0.0
        conf = round(_clamp(32.0 + 30.0 * agreement + 22.0 * strength + bt_bonus, 20.0, 90.0), 1)

        pred_price = None
        low_price = None
        high_price = None
        if current_price is not None:
            pred_price = round(current_price * (1.0 + blended_ret / 100.0), 2)
            if wave_res.q15_pct is not None and wave_res.q85_pct is not None:
                low_pct = min(wave_res.q15_pct, blended_ret - 0.35 * daily_vol_pct * h_scale)
                high_pct = max(wave_res.q85_pct, blended_ret + 0.35 * daily_vol_pct * h_scale)
                low_price = round(current_price * (1.0 + low_pct / 100.0), 2)
                high_price = round(current_price * (1.0 + high_pct / 100.0), 2)
            else:
                band_pct = daily_vol_pct * h_scale * 0.85
                low_price = round(current_price * (1.0 + (blended_ret - band_pct) / 100.0), 2)
                high_price = round(current_price * (1.0 + (blended_ret + band_pct) / 100.0), 2)

        horizons_out[h_label] = HorizonForecast(
            horizon=h_label,
            verdict=verdict,
            direction=direction,
            predicted_return_pct=blended_ret,
            predicted_price_usd=pred_price,
            price_low_usd=low_price,
            price_high_usd=high_price,
            confidence_pct=conf,
            pillar_return_pct=pillar_ret,
            ml_return_pct=ml_pred,
            ml_weight=ml_weight,
            backtest_hit_rate_pct=hit_rate,
            backtest_mae_pct=mae,
            backtest_samples=bt_samples,
            high_conviction_hit_rate_pct=hc_hit,
            high_conviction_samples=hc_samples,
            indep_hit_rate_pct=wave_res.indep_hit_rate_pct,
            indep_samples=wave_res.indep_samples,
            indep_p_value=wave_res.indep_p_value,
            up_wave_prob_pct=wave_res.up_wave_prob_pct,
            down_wave_prob_pct=wave_res.down_wave_prob_pct,
            range_prob_pct=wave_res.range_prob_pct,
            wave_state=wave_state,
            model_name=model_name,
        )

    primary = horizons_out["5d"]
    price_clause = (
        f" (~${primary.predicted_price_usd:.2f} vs ${current_price:.2f} now)"
        if (primary.predicted_price_usd is not None and current_price is not None)
        else ""
    )
    strongest_pillar = max(pillars, key=lambda p: abs(p.score))
    weakest_counter = min(pillars, key=lambda p: p.score if primary.direction == "bullish" else -p.score)

    counter_clause = ""
    if (primary.direction == "bullish" and weakest_counter.score <= -15) or (
        primary.direction == "bearish" and weakest_counter.score >= 15
    ):
        counter_clause = f", partly offset by {weakest_counter.label.lower()}"

    wave_prob_clause = ""
    if primary.up_wave_prob_pct is not None and primary.down_wave_prob_pct is not None:
        wave_prob_clause = (
            f" · Wave probs: {primary.up_wave_prob_pct:.0f}% Up / "
            f"{primary.down_wave_prob_pct:.0f}% Down"
        )

    headline_summary = (
        f"{primary.wave_state.upper()} ({primary.predicted_return_pct:+.2f}% over next 5d{price_clause}) — "
        f"led by {strongest_pillar.label.lower()}{counter_clause}{wave_prob_clause}."
    )

    top_drivers = (news_drivers + physical_drivers)[:5]

    return BottomLineForecastResponse(
        symbol=symbol,
        symbol_label=sym_label,
        as_of=now,
        current_price_usd=current_price,
        price_date=price_date,
        daily_vol_pct=daily_vol_pct,
        composite_score=composite_score,
        primary_horizon="5d",
        verdict=primary.verdict,
        direction=primary.direction,
        headline_summary=headline_summary,
        horizons=horizons_out,
        pillars=pillars,
        top_drivers=top_drivers,
        active_news_count_48h=len(active_events),
        analog_memory_size=len(memory.records),
        latest_briefing_bias=latest_briefing.market_bias if latest_briefing else None,
        latest_briefing_summary=latest_briefing.executive_summary if latest_briefing else None,
    )


def _assemble_gbt_daily_rows(
    price_rows: list[OilPrice],
    macro_by_date: dict[Any, OilMacroDaily],
    pw_by_date: dict[Any, dict[str, float]],
    fundamentals_rows: list[OilFundamentalsWeekly],
    news_records: list[Any],
) -> list[dict[str, Any]]:
    """Build 5-year daily feature rows with strict +3d CFTC / +5d EIA publication lags and volume-normalized news polarity."""
    news_dir_num: dict = {}
    news_dir_den: dict = {}
    news_shocks_by_date: dict = {}
    news_total_by_date: dict = {}
    news_disrupt_by_date: dict = {}
    for rec in news_records:
        d = rec.market_aligned_at.date()
        w = max(0.2, rec.prior_rank_score / 100.0)
        news_dir_num[d] = news_dir_num.get(d, 0.0) + rec.effective_direction * w
        news_dir_den[d] = news_dir_den.get(d, 0.0) + w
        news_total_by_date[d] = news_total_by_date.get(d, 0) + 1
        if rec.event_type in ("chokepoint_attack_blockade", "refinery_pipeline_outage"):
            news_disrupt_by_date[d] = news_disrupt_by_date.get(d, 0) + 1
        if rec.prior_rank_score >= 55.0:
            news_shocks_by_date[d] = news_shocks_by_date.get(d, 0) + 1
    news_dir_by_date = {
        d: round(max(-1.0, min(1.0, news_dir_num[d] / news_dir_den[d])), 4)
        for d in news_dir_num
        if news_dir_den.get(d, 0.0) > 0
    }
    disruption_share_by_date = {
        d: round(news_disrupt_by_date.get(d, 0) / news_total_by_date[d], 4)
        for d in news_total_by_date
        if news_total_by_date[d] > 0
    }

    cftc_series = [
        (f.report_date + timedelta(days=3), f.cftc_mm_net_pct_oi)
        for f in fundamentals_rows
        if f.cftc_mm_net_pct_oi is not None
    ]
    eia_series = [
        (f.report_date + timedelta(days=5), f.eia_crude_stocks_chg_kbbl, f.eia_refinery_util_pct)
        for f in fundamentals_rows
        if f.eia_crude_stocks_chg_kbbl is not None or f.eia_refinery_util_pct is not None
    ]
    c_idx = 0
    e_idx = 0
    cur_cftc: Optional[float] = None
    cur_eia_chg: Optional[float] = None
    cur_eia_util: Optional[float] = None

    gbt_daily_rows: list[dict[str, Any]] = []
    for p in price_rows:
        while c_idx < len(cftc_series) and cftc_series[c_idx][0] <= p.price_date:
            cur_cftc = cftc_series[c_idx][1]
            c_idx += 1
        while e_idx < len(eia_series) and eia_series[e_idx][0] <= p.price_date:
            if eia_series[e_idx][1] is not None:
                cur_eia_chg = eia_series[e_idx][1]
            if eia_series[e_idx][2] is not None:
                cur_eia_util = eia_series[e_idx][2]
            e_idx += 1

        m = macro_by_date.get(p.price_date)
        pw = pw_by_date.get(p.price_date)
        if not pw:
            # Backward-only forward-fill up to 5d to cover IMF PortWatch's ~3-4 day satellite lag
            for lag in range(1, 6):
                prev_pw = pw_by_date.get(p.price_date - timedelta(days=lag))
                if prev_pw:
                    pw = prev_pw
                    break
        pw = pw or {}
        gbt_daily_rows.append({
            "date": p.price_date,
            "brent_close_usd": p.brent_close_usd,
            "wti_close_usd": p.wti_close_usd,
            "crack_spread_321_usd": m.crack_spread_321_usd if m else None,
            "brent_wti_spread_usd": (
                m.brent_wti_spread_usd
                if (m and m.brent_wti_spread_usd is not None)
                else (
                    round(p.brent_close_usd - p.wti_close_usd, 2)
                    if (p.brent_close_usd is not None and p.wti_close_usd is not None)
                    else None
                )
            ),
            "dxy_close": m.dxy_close if m else None,
            "vix_close": m.vix_close if m else None,
            "tanker_etf_close": m.tanker_etf_close if m else None,
            "energy_etf_close": m.energy_etf_close if m else None,
            "copper_close": m.copper_close if m else None,
            "china_etf_close": m.china_etf_close if m else None,
            "natgas_close": m.natgas_close if m else None,
            "us10y_yield": m.us10y_yield if m else None,
            "product_tanker_close": m.product_tanker_close if m else None,
            "curve_backwardation_proxy": m.curve_backwardation_proxy if m else None,
            "cftc_mm_net_pct_oi": cur_cftc,
            "eia_crude_stocks_chg_kbbl": cur_eia_chg,
            "eia_refinery_util_pct": cur_eia_util,
            # None on zero-news days so 3d/7d averages ignore silent days (eliminates 2022 vs 2026 volume drift)
            "news_dir_1d": news_dir_by_date.get(p.price_date),
            "disruption_news_1d": disruption_share_by_date.get(p.price_date),
            "news_shock_count_1d": news_shocks_by_date.get(p.price_date, 0),
            "pw_hormuz_tankers": pw.get("pw_hormuz_tankers"),
            "pw_hormuz_capacity": pw.get("pw_hormuz_capacity"),
            "pw_bab_tankers": pw.get("pw_bab_tankers"),
            "pw_suez_tankers": pw.get("pw_suez_tankers"),
            "pw_cape_tankers": pw.get("pw_cape_tankers"),
            "pw_global_tankers": pw.get("pw_global_tankers"),
        })
    return gbt_daily_rows


async def _load_5y_daily_rows(
    db: AsyncSession,
    symbol: str = "BZ=F",
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Load 5-year daily feature rows and return (daily_rows, data_coverage_counts)."""
    now = datetime.now(timezone.utc)
    cutoff_5y = (now - timedelta(days=1850)).date()

    price_rows = list(
        (
            await db.execute(
                select(OilPrice)
                .where(OilPrice.price_date >= cutoff_5y)
                .order_by(OilPrice.price_date.asc())
            )
        ).scalars().all()
    )
    macro_rows = list(
        (
            await db.execute(
                select(OilMacroDaily)
                .where(OilMacroDaily.date >= cutoff_5y)
                .order_by(OilMacroDaily.date.asc())
            )
        ).scalars().all()
    )
    fundamentals_rows = list(
        (
            await db.execute(
                select(OilFundamentalsWeekly)
                .where(OilFundamentalsWeekly.report_date >= cutoff_5y)
                .order_by(OilFundamentalsWeekly.report_date.asc())
            )
        ).scalars().all()
    )
    pw_rows = list(
        (
            await db.execute(
                select(
                    PortWatchChokepointDaily.date,
                    PortWatchChokepointDaily.portname,
                    PortWatchChokepointDaily.n_tanker,
                    PortWatchChokepointDaily.capacity_tanker,
                )
                .where(PortWatchChokepointDaily.date >= cutoff_5y)
            )
        ).all()
    )
    pw_by_date: dict = {}
    for pw_d, pname, n_t, cap_t in pw_rows:
        bucket = pw_by_date.setdefault(
            pw_d,
            {
                "pw_hormuz_tankers": 0.0,
                "pw_hormuz_capacity": 0.0,
                "pw_bab_tankers": 0.0,
                "pw_suez_tankers": 0.0,
                "pw_cape_tankers": 0.0,
                "pw_global_tankers": 0.0,
            },
        )
        nt_val = float(n_t or 0.0)
        cap_val = float(cap_t or 0.0)
        bucket["pw_global_tankers"] += nt_val
        p_low = (pname or "").lower()
        if "hormuz" in p_low:
            bucket["pw_hormuz_tankers"] += nt_val
            bucket["pw_hormuz_capacity"] += cap_val
        elif "mandeb" in p_low:
            bucket["pw_bab_tankers"] += nt_val
        elif "suez" in p_low:
            bucket["pw_suez_tankers"] += nt_val
        elif "good hope" in p_low or "cape" in p_low:
            bucket["pw_cape_tankers"] += nt_val

    memory = await build_news_analog_memory(db, symbol=symbol)
    daily_rows = _assemble_gbt_daily_rows(
        price_rows=price_rows,
        macro_by_date={m.date: m for m in macro_rows},
        pw_by_date=pw_by_date,
        fundamentals_rows=fundamentals_rows,
        news_records=memory.records,
    )
    coverage = {
        "trading_days": len(daily_rows),
        "macro_days": len(macro_rows),
        "portwatch_days": len(pw_by_date),
        "cftc_weeks": sum(1 for f in fundamentals_rows if f.cftc_mm_net_pct_oi is not None),
        "eia_weeks": sum(1 for f in fundamentals_rows if f.eia_crude_stocks_chg_kbbl is not None),
        "news_events": len(memory.records),
    }
    return daily_rows, coverage


async def build_feature_importance_report(
    db: AsyncSession,
    symbol: str = "BZ=F",
    horizon: str = "5d",
    include_all_candidates: bool = True,
) -> dict[str, Any]:
    """Compute strictly out-of-sample permutation feature importance across 5-year history."""
    sym_label = "Brent Crude" if symbol == "BZ=F" else "WTI Crude"
    price_key = "brent_close_usd" if symbol == "BZ=F" else "wti_close_usd"
    horizon_days = {"3d": 3, "5d": 5, "7d": 7, "14d": 14}.get(horizon, 5)
    daily_rows, _ = await _load_5y_daily_rows(db, symbol=symbol)
    report = compute_oos_feature_importance(
        daily_rows=daily_rows,
        price_key=price_key,
        horizon_days=horizon_days,
        include_all_candidates=include_all_candidates,
        n_repeats=5,
    )
    return {
        "symbol": symbol,
        "symbol_label": sym_label,
        "horizon": horizon,
        "include_all_candidates": include_all_candidates,
        **report,
    }


async def build_model_benchmark_report(db: AsyncSession) -> dict[str, Any]:
    """Head-to-head full-4y OOS benchmark: Expanding vs Rolling 300d vs Extreme-Only vs Volatility Alert."""
    daily_rows, coverage = await _load_5y_daily_rows(db, symbol="BZ=F")
    results: dict[str, dict[str, Any]] = {}

    def _pack(res) -> dict[str, Any]:
        return {
            "indep_hit_rate_pct": res.indep_hit_rate_pct,
            "indep_samples": res.indep_samples,
            "indep_p_value": res.indep_p_value,
            "rolling_hit_rate_pct": res.signal_hit_rate_pct,
            "rolling_samples": res.signal_samples,
            "mae_pct": res.mae_pct,
        }

    for sym, price_key in (("BZ=F", "brent_close_usd"), ("CL=F", "wti_close_usd")):
        sym_out: dict[str, Any] = {}
        for h_label, h_days in FORECAST_HORIZONS:
            exp_10f = run_walkforward_regularized_wave(
                daily_rows, price_key=price_key, horizon_days=h_days
            )
            rol_10f = run_walkforward_regularized_wave(
                daily_rows, price_key=price_key, horizon_days=h_days, train_window_days=300
            )
            rol_ext = run_walkforward_regularized_wave(
                daily_rows,
                price_key=price_key,
                horizon_days=h_days,
                train_window_days=300,
                min_prob=0.55,
                min_margin=0.22,
            )
            vol_alert = run_walkforward_volatility_alert(
                daily_rows, price_key=price_key, horizon_days=h_days, train_window_days=300
            )
            har_vol = run_har_volatility_ensemble(
                daily_rows, price_key=price_key, horizon_days=h_days
            )
            spike_det = run_walkforward_spike_detector(
                daily_rows, price_key=price_key, horizon_days=h_days
            )
            sym_out[h_label] = {
                "expanding_10f": _pack(exp_10f),
                "rolling_300d_10f": _pack(rol_10f),
                "rolling_300d_extreme": _pack(rol_ext),
                "big_move_volatility": vol_alert,
                "har_volatility_ensemble": har_vol,
                "upward_spike_detector": spike_det,
            }
        # Test monthly fundamental horizons (20d & 30d) per Baumeister & Kilian literature
        sym_out["20d_fundamental"] = _pack(
            run_walkforward_regularized_wave(daily_rows, price_key=price_key, horizon_days=20)
        )
        sym_out["30d_fundamental"] = _pack(
            run_walkforward_regularized_wave(daily_rows, price_key=price_key, horizon_days=30)
        )
        results[sym] = sym_out
    return {
        "data_coverage": coverage,
        "horizons": [h for h, _ in FORECAST_HORIZONS],
        "benchmarks": results,
    }


