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

from app.models.global_oil_features import OilMarketSnapshot, OilPrice, StraitRiskDaily
from app.models.news import AIMarketBriefing
from app.schemas.forecast import (
    BottomLineForecastResponse,
    ForecastDriver,
    ForecastPillar,
    HorizonForecast,
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
    ("24h", 1),
    ("3d", 3),
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

    # 1. Load daily snapshots (up to 365d) + OilPrice + StraitRiskDaily
    cutoff_date = (now - timedelta(days=365)).date()
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
                .where(OilPrice.price_date >= cutoff_date)
                .order_by(OilPrice.price_date.asc())
            )
        ).scalars().all()
    )
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

    # 5. Pillar 4 — Market Momentum & Volatility
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
    mom_score = round(_clamp((0.6 * mom_3d_pct + 0.4 * mom_7d_pct) * 14.0, -100.0, 100.0), 1)
    mom_metrics = [
        f"3d trend: {mom_3d_pct:+.2f}% · 7d trend: {mom_7d_pct:+.2f}%",
        f"20d realized daily volatility: {daily_vol_pct:.2f}%",
    ]
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
        label="Price Momentum & Regime",
        score=mom_score,
        weight=PILLAR_WEIGHTS["market_momentum"],
        direction=_direction_from_score(mom_score),
        summary=(
            f"{sym_label} is in an upward short-term trend ({mom_7d_pct:+.2f}% over 7d)."
            if mom_score >= 15
            else f"{sym_label} is trading sideways ({mom_7d_pct:+.2f}% over 7d)."
            if mom_score > -15
            else f"{sym_label} faces downward price momentum ({mom_7d_pct:+.2f}% over 7d)."
        ),
        metrics=mom_metrics,
    )

    pillars = [news_pillar, cp_pillar, df_pillar, mom_pillar]
    composite_score = round(sum(p.score * p.weight for p in pillars), 1)

    # 6. Multi-horizon forecasts blending Pillars + News Analogs + Walk-Forward Ridge
    news_ret_map = {"24h": news_ret_24h, "3d": news_ret_3d, "7d": news_ret_7d}
    horizons_out: dict[str, HorizonForecast] = {}

    for h_label, h_days in FORECAST_HORIZONS:
        h_scale = math.sqrt(h_days)
        # Pillar-implied move: combine non-news pillars (vol-scaled) with direct news analog move
        non_news_score = (
            cp_pillar.score * cp_pillar.weight
            + df_pillar.score * df_pillar.weight
            + mom_pillar.score * mom_pillar.weight
        ) / (1.0 - news_pillar.weight)
        non_news_ret = (non_news_score / 100.0) * daily_vol_pct * h_scale * 0.9
        pillar_ret = round(
            news_pillar.weight * news_ret_map[h_label] + (1.0 - news_pillar.weight) * non_news_ret,
            2,
        )

        ml_pred, hit_rate, mae, bt_samples = run_walkforward_ridge(
            snapshot_dicts=snapshot_dicts,
            price_key=price_key,
            horizon_days=h_days,
        )

        # Weight ML higher only as out-of-sample history grows and shows edge
        if ml_pred is not None and bt_samples >= 10:
            edge_mult = 1.0 if (hit_rate is not None and hit_rate >= 52.0) else 0.5
            ml_weight = round(min(0.45, (bt_samples / 60.0) * 0.45) * edge_mult, 2)
            blended_ret = round((1.0 - ml_weight) * pillar_ret + ml_weight * ml_pred, 2)
        else:
            ml_weight = 0.0
            blended_ret = pillar_ret

        blended_ret = round(_clamp(blended_ret, -12.0, 12.0), 2)
        verdict, direction = _verdict_from_return(blended_ret, h_days)

        # Confidence combines pillar agreement + signal strength + optional ML backtest edge
        signs = [1 if p.score > 10 else (-1 if p.score < -10 else 0) for p in pillars]
        non_zero_signs = [s for s in signs if s != 0]
        target_sign = 1 if blended_ret >= 0 else -1
        agreement = (
            sum(1 for s in non_zero_signs if s == target_sign) / len(non_zero_signs)
            if non_zero_signs
            else 0.5
        )
        strength = min(1.0, abs(composite_score) / 65.0)
        bt_bonus = ((hit_rate - 50.0) / 50.0) * 15.0 if (hit_rate is not None and bt_samples >= 10) else 0.0
        conf = round(_clamp(32.0 + 30.0 * agreement + 22.0 * strength + bt_bonus, 20.0, 88.0), 1)

        pred_price = None
        low_price = None
        high_price = None
        if current_price is not None:
            pred_price = round(current_price * (1.0 + blended_ret / 100.0), 2)
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
        )

    primary = horizons_out["3d"]
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

    headline_summary = (
        f"{primary.verdict.upper()} ({primary.predicted_return_pct:+.2f}% over 3d{price_clause}) — "
        f"led by {strongest_pillar.label.lower()}{counter_clause}."
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
        primary_horizon="3d",
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
