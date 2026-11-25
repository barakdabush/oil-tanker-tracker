"""5-Year Multi-Factor Gradient Boosted Tree (HistGradientBoostingRegressor) Oil Price Model.

Combines 5 years (~1,250 trading days) of:
- Crude futures regime & "priced-in" features (returns, MA distances, realized vol, RSI-14)
- Refining margins (3-2-1 Crack Spread level & 5d/20d change) & Brent-WTI spread
- Macro & shipping equities (DXY, VIX, Frontline VLCC tanker stock FRO, Energy ETF XLE)
- 5-year daily aggregated News Intelligence directional pressure & shock counts

Runs strictly out-of-sample expanding-window walk-forward evaluation (zero future leakage)
and fits quantile GBT models (P15 / P85) for asymmetric tail-risk price bands.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Optional

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor

FEATURE_NAMES: tuple[str, ...] = (
    "ret_1d",
    "ret_3d",
    "ret_5d",
    "ret_10d",
    "ret_20d",
    "dist_ma20",
    "dist_ma50",
    "vol_5d",
    "vol_20d",
    "vol_ratio",
    "rsi_14",
    "crack_321_level",
    "crack_321_chg_5d",
    "crack_321_chg_20d",
    "brent_wti_spread",
    "brent_wti_chg_5d",
    "dxy_ret_5d",
    "vix_level",
    "vix_chg_5d",
    "fro_ret_5d",
    "fro_ret_20d",
    "xle_ret_5d",
    "news_dir_1d",
    "news_dir_3d",
    "news_dir_7d",
    "news_shock_count_3d",
    # Stationary IMF PortWatch physical tanker flow ratios & z-scores (zero AIS source drift)
    "pw_hormuz_7d_vs_30d",
    "pw_hormuz_cap_7d_vs_30d",
    "pw_bab_7d_vs_30d",
    "pw_suez_7d_vs_30d",
    "pw_cape_7d_vs_30d",
    "pw_cape_vs_suez_share",
    "pw_global_tankers_7d_vs_30d",
    "pw_hormuz_cap_z30",
    # Global demand proxies, futures curve structure, CFTC hedge-fund positioning & EIA physical storage
    "copper_ret_5d",
    "copper_ret_20d",
    "china_ret_5d",
    "china_ret_20d",
    "natgas_ret_5d",
    "us10y_chg_5d",
    "stng_ret_5d",
    "curve_chg_5d",
    "curve_chg_20d",
    "cftc_mm_net_pct_oi",
    "cftc_mm_net_z52",
    "eia_stocks_chg_kbbl",
    "eia_refinery_util_pct",
)


@dataclass
class GBTForecastResult:
    latest_pred_pct: Optional[float]
    q15_pct: Optional[float]
    q85_pct: Optional[float]
    hit_rate_pct: Optional[float]
    mae_pct: Optional[float]
    samples: int
    high_conviction_hit_rate_pct: Optional[float]
    high_conviction_samples: int
    training_rows: int


def _safe_log_ret(arr: np.ndarray, idx: int, lag: int) -> float:
    if idx < lag:
        return np.nan
    a, b = arr[idx], arr[idx - lag]
    if not (math.isfinite(a) and math.isfinite(b)) or a <= 0 or b <= 0:
        return np.nan
    return math.log(a / b)


def _safe_diff(arr: np.ndarray, idx: int, lag: int) -> float:
    if idx < lag:
        return np.nan
    a, b = arr[idx], arr[idx - lag]
    if not (math.isfinite(a) and math.isfinite(b)):
        return np.nan
    return float(a - b)


def _rolling_ratio_7_30(arr: np.ndarray, idx: int) -> float:
    """Scale-invariant ratio of 7-day rolling mean to 30-day rolling mean (minus 1.0)."""
    if idx < 10:
        return np.nan
    s7 = arr[max(0, idx - 6) : idx + 1]
    s30 = arr[max(0, idx - 29) : idx + 1]
    v7 = s7[np.isfinite(s7)]
    v30 = s30[np.isfinite(s30)]
    if len(v7) < 3 or len(v30) < 10:
        return np.nan
    m30 = float(np.mean(v30))
    if m30 <= 1e-6:
        return np.nan
    return float(np.mean(v7) / m30 - 1.0)


def _rolling_z30(arr: np.ndarray, idx: int) -> float:
    """30-day rolling z-score of the 7-day smoothed series."""
    if idx < 15:
        return np.nan
    s7 = arr[max(0, idx - 6) : idx + 1]
    s30 = arr[max(0, idx - 29) : idx + 1]
    v7 = s7[np.isfinite(s7)]
    v30 = s30[np.isfinite(s30)]
    if len(v7) < 3 or len(v30) < 12:
        return np.nan
    std = float(np.std(v30))
    if std <= 1e-6:
        return 0.0
    return float(max(-3.5, min(3.5, (float(np.mean(v7)) - float(np.mean(v30))) / std)))


def _rolling_z_window(arr: np.ndarray, idx: int, window: int = 252) -> float:
    """Rolling z-score of current value vs trailing ``window`` trading days."""
    if idx < 30 or not math.isfinite(arr[idx]):
        return np.nan
    sl = arr[max(0, idx - window + 1) : idx + 1]
    valid = sl[np.isfinite(sl)]
    if len(valid) < 20:
        return np.nan
    std = float(np.std(valid))
    if std <= 1e-6:
        return 0.0
    return float(max(-3.5, min(3.5, (float(arr[idx]) - float(np.mean(valid))) / std)))


def build_gbt_feature_matrix(
    daily_rows: list[dict[str, Any]],
    price_key: str,
) -> tuple[np.ndarray, np.ndarray, list[date]]:
    """Build strictly backward-looking (n x F) feature matrix and price array from chronologically sorted daily rows."""
    priced = [
        r for r in daily_rows
        if r.get(price_key) is not None and float(r[price_key]) > 0
    ]
    n = len(priced)
    if n == 0:
        return np.zeros((0, len(FEATURE_NAMES))), np.zeros(0), []

    dates = [r["date"] for r in priced]
    prices = np.array([float(r[price_key]) for r in priced], dtype=np.float64)

    def _col(key: str) -> np.ndarray:
        return np.array(
            [float(r[key]) if r.get(key) is not None else np.nan for r in priced],
            dtype=np.float64,
        )

    crack = _col("crack_spread_321_usd")
    bw = _col("brent_wti_spread_usd")
    dxy = _col("dxy_close")
    vix = _col("vix_close")
    fro = _col("tanker_etf_close")
    xle = _col("energy_etf_close")
    news_1d = _col("news_dir_1d")
    news_shocks = _col("news_shock_count_1d")

    # IMF PortWatch satellite-AIS daily tanker series (uniform methodology across 5+ years)
    pw_hormuz = _col("pw_hormuz_tankers")
    pw_hormuz_cap = _col("pw_hormuz_capacity")
    pw_bab = _col("pw_bab_tankers")
    pw_suez = _col("pw_suez_tankers")
    pw_cape = _col("pw_cape_tankers")
    pw_global = _col("pw_global_tankers")

    # Global demand, futures curve, CFTC COT & EIA physical storage series
    copper = _col("copper_close")
    china = _col("china_etf_close")
    natgas = _col("natgas_close")
    us10y = _col("us10y_yield")
    stng = _col("product_tanker_close")
    curve = _col("curve_backwardation_proxy")
    cftc_net_pct = _col("cftc_mm_net_pct_oi")
    eia_chg = _col("eia_crude_stocks_chg_kbbl")
    eia_util = _col("eia_refinery_util_pct")

    daily_rets = np.full(n, np.nan, dtype=np.float64)
    for i in range(1, n):
        daily_rets[i] = math.log(prices[i] / prices[i - 1])

    X = np.full((n, len(FEATURE_NAMES)), np.nan, dtype=np.float64)
    for i in range(n):
        r1 = _safe_log_ret(prices, i, 1)
        r3 = _safe_log_ret(prices, i, 3)
        r5 = _safe_log_ret(prices, i, 5)
        r10 = _safe_log_ret(prices, i, 10)
        r20 = _safe_log_ret(prices, i, 20)

        # Moving-average distances ("priced-in" / mean-reversion regime)
        ma20 = float(np.mean(prices[max(0, i - 19) : i + 1])) if i >= 5 else np.nan
        ma50 = float(np.mean(prices[max(0, i - 49) : i + 1])) if i >= 15 else np.nan
        dist_ma20 = (prices[i] / ma20 - 1.0) if (math.isfinite(ma20) and ma20 > 0) else np.nan
        dist_ma50 = (prices[i] / ma50 - 1.0) if (math.isfinite(ma50) and ma50 > 0) else np.nan

        # Volatility regime
        v5_slice = daily_rets[max(1, i - 4) : i + 1]
        v5_valid = v5_slice[np.isfinite(v5_slice)]
        vol5 = float(np.std(v5_valid, ddof=1)) if len(v5_valid) >= 3 else np.nan

        v20_slice = daily_rets[max(1, i - 19) : i + 1]
        v20_valid = v20_slice[np.isfinite(v20_slice)]
        vol20 = float(np.std(v20_valid, ddof=1)) if len(v20_valid) >= 8 else np.nan
        vol_ratio = (vol5 / vol20) if (math.isfinite(vol5) and math.isfinite(vol20) and vol20 > 1e-6) else np.nan

        # RSI-14
        if i >= 14:
            r14 = daily_rets[i - 13 : i + 1]
            r14 = r14[np.isfinite(r14)]
            if len(r14) >= 10:
                gains = float(np.mean(np.maximum(r14, 0.0)))
                losses = float(np.mean(np.maximum(-r14, 0.0)))
                rsi14 = 100.0 - (100.0 / (1.0 + gains / losses)) if losses > 1e-9 else 100.0
            else:
                rsi14 = np.nan
        else:
            rsi14 = np.nan

        # Multi-day news pressure (3d & 7d rolling sums)
        n1 = news_1d[i] if math.isfinite(news_1d[i]) else 0.0
        n3_slice = news_1d[max(0, i - 2) : i + 1]
        n3 = float(np.nansum(n3_slice)) if np.any(np.isfinite(n3_slice)) else 0.0
        n7_slice = news_1d[max(0, i - 6) : i + 1]
        n7 = float(np.nansum(n7_slice)) if np.any(np.isfinite(n7_slice)) else 0.0
        sh3_slice = news_shocks[max(0, i - 2) : i + 1]
        sh3 = float(np.nansum(sh3_slice)) if np.any(np.isfinite(sh3_slice)) else 0.0

        # Scale-invariant PortWatch rerouting share: Cape / (Cape + Suez + Bab) over trailing 7d
        c7 = pw_cape[max(0, i - 6) : i + 1]
        s7 = pw_suez[max(0, i - 6) : i + 1]
        b7 = pw_bab[max(0, i - 6) : i + 1]
        if np.any(np.isfinite(c7)) and np.any(np.isfinite(s7)):
            c_sum = float(np.nansum(c7))
            tot_reroute = c_sum + float(np.nansum(s7)) + float(np.nansum(b7))
            cape_share = (c_sum / tot_reroute) if tot_reroute > 0 else np.nan
        else:
            cape_share = np.nan

        X[i] = [
            r1,
            r3,
            r5,
            r10,
            r20,
            dist_ma20,
            dist_ma50,
            vol5,
            vol20,
            vol_ratio,
            rsi14,
            crack[i],
            _safe_diff(crack, i, 5),
            _safe_diff(crack, i, 20),
            bw[i],
            _safe_diff(bw, i, 5),
            _safe_log_ret(dxy, i, 5),
            vix[i],
            _safe_diff(vix, i, 5),
            _safe_log_ret(fro, i, 5),
            _safe_log_ret(fro, i, 20),
            _safe_log_ret(xle, i, 5),
            n1,
            n3,
            n7,
            sh3,
            _rolling_ratio_7_30(pw_hormuz, i),
            _rolling_ratio_7_30(pw_hormuz_cap, i),
            _rolling_ratio_7_30(pw_bab, i),
            _rolling_ratio_7_30(pw_suez, i),
            _rolling_ratio_7_30(pw_cape, i),
            cape_share,
            _rolling_ratio_7_30(pw_global, i),
            _rolling_z30(pw_hormuz_cap, i),
            _safe_log_ret(copper, i, 5),
            _safe_log_ret(copper, i, 20),
            _safe_log_ret(china, i, 5),
            _safe_log_ret(china, i, 20),
            _safe_log_ret(natgas, i, 5),
            _safe_diff(us10y, i, 5),
            _safe_log_ret(stng, i, 5),
            _safe_log_ret(curve, i, 5),
            _safe_log_ret(curve, i, 20),
            cftc_net_pct[i],
            _rolling_z_window(cftc_net_pct, i, 252),
            eia_chg[i],
            eia_util[i],
        ]

    return X, prices, dates


def _make_gbt(loss: str = "squared_error", quantile: Optional[float] = None) -> HistGradientBoostingRegressor:
    kwargs: dict[str, Any] = {
        "loss": loss,
        "max_iter": 75,
        "max_depth": 3,
        "learning_rate": 0.05,
        "l2_regularization": 3.0,
        "min_samples_leaf": 20,
        "max_bins": 128,
        "random_state": 42,
    }
    if quantile is not None:
        kwargs["quantile"] = quantile
    return HistGradientBoostingRegressor(**kwargs)


def run_walkforward_gbt(
    daily_rows: list[dict[str, Any]],
    price_key: str,
    horizon_days: int,
    min_train_rows: int = 220,
    refit_every: int = 25,
    max_eval_days: int = 400,
) -> GBTForecastResult:
    """Strictly expanding-window walk-forward HistGradientBoostingRegressor backtest & live forecast."""
    X, prices, _ = build_gbt_feature_matrix(daily_rows, price_key=price_key)
    n = len(prices)
    if n < min_train_rows + horizon_days + 20:
        return GBTForecastResult(
            latest_pred_pct=None,
            q15_pct=None,
            q85_pct=None,
            hit_rate_pct=None,
            mae_pct=None,
            samples=0,
            high_conviction_hit_rate_pct=None,
            high_conviction_samples=0,
            training_rows=n,
        )

    y_fwd = np.full(n, np.nan, dtype=np.float64)
    for i in range(n - horizon_days):
        if prices[i] > 0 and prices[i + horizon_days] > 0:
            y_fwd[i] = math.log(prices[i + horizon_days] / prices[i])

    # Walk-forward evaluation over the most recent `max_eval_days` matured trading days
    eval_end = n - horizon_days
    eval_start = max(min_train_rows + horizon_days, eval_end - max_eval_days)

    hits = 0
    abs_errs: list[float] = []
    hc_hits = 0
    hc_total = 0
    hc_threshold = 0.55 * math.sqrt(max(1, horizon_days))

    model: Optional[HistGradientBoostingRegressor] = None
    last_fit_t = -9999

    for t in range(eval_start, eval_end):
        if not math.isfinite(y_fwd[t]):
            continue
        # Strictly anti-leakage training cutoff: target i + horizon_days <= t
        train_end = t - horizon_days + 1
        if model is None or (t - last_fit_t) >= refit_every:
            X_tr = X[:train_end]
            y_tr = y_fwd[:train_end]
            mask = np.isfinite(y_tr)
            if int(mask.sum()) < min_train_rows:
                continue
            model = _make_gbt(loss="squared_error")
            model.fit(X_tr[mask], y_tr[mask])
            last_fit_t = t

        pred_log = float(model.predict(X[t : t + 1])[0])
        pred_pct = (math.exp(max(-0.20, min(0.20, pred_log))) - 1.0) * 100.0
        actual_pct = (math.exp(float(y_fwd[t])) - 1.0) * 100.0

        is_hit = (pred_pct >= 0 and actual_pct >= 0) or (pred_pct < 0 and actual_pct < 0)
        if is_hit:
            hits += 1
        abs_errs.append(abs(pred_pct - actual_pct))

        if abs(pred_pct) >= hc_threshold:
            hc_total += 1
            if is_hit:
                hc_hits += 1

    # Fit final production models (point + P15/P85 quantiles) on all matured rows up to today
    X_full = X[:eval_end]
    y_full = y_fwd[:eval_end]
    full_mask = np.isfinite(y_full)
    if int(full_mask.sum()) < min_train_rows:
        return GBTForecastResult(
            latest_pred_pct=None,
            q15_pct=None,
            q85_pct=None,
            hit_rate_pct=None,
            mae_pct=None,
            samples=0,
            high_conviction_hit_rate_pct=None,
            high_conviction_samples=0,
            training_rows=int(full_mask.sum()),
        )

    final_model = _make_gbt(loss="squared_error")
    final_model.fit(X_full[full_mask], y_full[full_mask])
    pred_log_now = float(final_model.predict(X[n - 1 : n])[0])
    latest_pred_pct = round((math.exp(max(-0.20, min(0.20, pred_log_now))) - 1.0) * 100.0, 2)

    q15_model = _make_gbt(loss="quantile", quantile=0.15)
    q85_model = _make_gbt(loss="quantile", quantile=0.85)
    q15_model.fit(X_full[full_mask], y_full[full_mask])
    q85_model.fit(X_full[full_mask], y_full[full_mask])
    q15_log = float(q15_model.predict(X[n - 1 : n])[0])
    q85_log = float(q85_model.predict(X[n - 1 : n])[0])
    q15_pct = round((math.exp(max(-0.25, min(0.25, q15_log))) - 1.0) * 100.0, 2)
    q85_pct = round((math.exp(max(-0.25, min(0.25, q85_log))) - 1.0) * 100.0, 2)
    if q15_pct > q85_pct:
        q15_pct, q85_pct = q85_pct, q15_pct

    samples = len(abs_errs)
    hit_rate = round((hits / samples) * 100.0, 1) if samples > 0 else None
    mae = round(sum(abs_errs) / samples, 2) if samples > 0 else None
    hc_hit_rate = round((hc_hits / hc_total) * 100.0, 1) if hc_total >= 10 else None

    return GBTForecastResult(
        latest_pred_pct=latest_pred_pct,
        q15_pct=q15_pct,
        q85_pct=q85_pct,
        hit_rate_pct=hit_rate,
        mae_pct=mae,
        samples=samples,
        high_conviction_hit_rate_pct=hc_hit_rate,
        high_conviction_samples=hc_total,
        training_rows=int(full_mask.sum()),
    )


@dataclass
class WaveForecastResult:
    wave_state: str  # "Bullish Wave" | "Bearish Wave" | "Range-Bound"
    up_wave_prob_pct: Optional[float]
    down_wave_prob_pct: Optional[float]
    range_prob_pct: Optional[float]
    expected_wave_pct: Optional[float]
    q15_pct: Optional[float]
    q85_pct: Optional[float]
    signal_hit_rate_pct: Optional[float]
    signal_samples: int
    high_conviction_hit_rate_pct: Optional[float]
    high_conviction_samples: int
    mae_pct: Optional[float]


def run_walkforward_wave_classifier(
    daily_rows: list[dict[str, Any]],
    price_key: str,
    horizon_days: int,
    min_train_rows: int = 220,
    refit_every: int = 25,
    max_eval_days: int = 400,
) -> WaveForecastResult:
    """Multi-day Oil Price Wave Detector (Bullish Wave / Range-Bound / Bearish Wave).

    Instead of noisy single-day close-to-close prediction, targets the smoothed
    forward H-day wave return ``ln(mean(P[t+1..t+H]) / P[t])`` using a 3-class
    HistGradientBoostingClassifier with strict expanding-window walk-forward validation.
    Abstains (Range-Bound) on quiet/ambiguous days and measures precision strictly on
    fired wave alerts.
    """
    X, prices, _ = build_gbt_feature_matrix(daily_rows, price_key=price_key)
    n = len(prices)
    if n < min_train_rows + horizon_days + 25:
        return WaveForecastResult(
            wave_state="Range-Bound",
            up_wave_prob_pct=None,
            down_wave_prob_pct=None,
            range_prob_pct=None,
            expected_wave_pct=None,
            q15_pct=None,
            q85_pct=None,
            signal_hit_rate_pct=None,
            signal_samples=0,
            high_conviction_hit_rate_pct=None,
            high_conviction_samples=0,
            mae_pct=None,
        )

    # Smoothed forward wave return over the next H trading days
    wave_ret = np.full(n, np.nan, dtype=np.float64)
    for i in range(n - horizon_days):
        fwd_window = prices[i + 1 : i + 1 + horizon_days]
        if prices[i] > 0 and len(fwd_window) == horizon_days and np.all(fwd_window > 0):
            wave_ret[i] = math.log(float(np.mean(fwd_window)) / prices[i])

    # Wave threshold scales with horizon (3d: 0.8%, 7d: 1.2%, 14d: 1.8%)
    wave_th = 0.008 if horizon_days <= 3 else (0.012 if horizon_days <= 7 else 0.018)

    # Labels: 0 = Down Wave, 1 = Range-Bound, 2 = Up Wave
    y_cls = np.full(n, -1, dtype=np.int64)
    for i in range(n - horizon_days):
        if not math.isfinite(wave_ret[i]):
            continue
        if wave_ret[i] >= wave_th:
            y_cls[i] = 2
        elif wave_ret[i] <= -wave_th:
            y_cls[i] = 0
        else:
            y_cls[i] = 1

    def _make_clf() -> HistGradientBoostingClassifier:
        return HistGradientBoostingClassifier(
            max_iter=80,
            max_depth=3,
            learning_rate=0.05,
            l2_regularization=3.0,
            min_samples_leaf=20,
            max_bins=128,
            random_state=42,
        )

    def _extract_probs(clf: HistGradientBoostingClassifier, x_row: np.ndarray) -> tuple[float, float, float]:
        probs = clf.predict_proba(x_row)[0]
        cls_map = {int(c): float(p) for c, p in zip(clf.classes_, probs)}
        return cls_map.get(0, 0.0), cls_map.get(1, 0.0), cls_map.get(2, 0.0)

    eval_end = n - horizon_days
    eval_start = max(min_train_rows + horizon_days, eval_end - max_eval_days)

    clf: Optional[HistGradientBoostingClassifier] = None
    last_fit_t = -9999
    avg_up_pct = 2.0
    avg_down_pct = -2.0

    sig_hits = 0
    sig_total = 0
    hc_hits = 0
    hc_total = 0
    abs_errs: list[float] = []

    for t in range(eval_start, eval_end):
        if y_cls[t] < 0 or not math.isfinite(wave_ret[t]):
            continue
        train_end = t - horizon_days + 1
        if clf is None or (t - last_fit_t) >= refit_every:
            y_tr = y_cls[:train_end]
            mask = y_tr >= 0
            if int(mask.sum()) < min_train_rows or len(set(y_tr[mask].tolist())) < 2:
                continue
            clf = _make_clf()
            clf.fit(X[:train_end][mask], y_tr[mask])
            # Empirical average up/down wave magnitudes from training set only
            w_tr = wave_ret[:train_end][mask]
            up_vals = w_tr[y_tr[mask] == 2]
            dn_vals = w_tr[y_tr[mask] == 0]
            if len(up_vals) > 0:
                avg_up_pct = float(np.mean(up_vals)) * 100.0
            if len(dn_vals) > 0:
                avg_down_pct = float(np.mean(dn_vals)) * 100.0
            last_fit_t = t

        p_down, p_range, p_up = _extract_probs(clf, X[t : t + 1])
        expected_pct = p_up * avg_up_pct + p_down * avg_down_pct
        actual_pct = (math.exp(float(wave_ret[t])) - 1.0) * 100.0

        # Fire a wave signal only when one side clearly dominates
        if p_up >= 0.45 and (p_up - p_down) >= 0.12:
            sig_total += 1
            is_hit = actual_pct > 0
            if is_hit:
                sig_hits += 1
            abs_errs.append(abs(expected_pct - actual_pct))
            if p_up >= 0.55 and (p_up - p_down) >= 0.22:
                hc_total += 1
                if is_hit:
                    hc_hits += 1
        elif p_down >= 0.45 and (p_down - p_up) >= 0.12:
            sig_total += 1
            is_hit = actual_pct < 0
            if is_hit:
                sig_hits += 1
            abs_errs.append(abs(expected_pct - actual_pct))
            if p_down >= 0.55 and (p_down - p_up) >= 0.22:
                hc_total += 1
                if is_hit:
                    hc_hits += 1

    # Fit final wave classifier on all matured rows up to today
    y_full = y_cls[:eval_end]
    full_mask = y_full >= 0
    if int(full_mask.sum()) < min_train_rows or len(set(y_full[full_mask].tolist())) < 2:
        return WaveForecastResult(
            wave_state="Range-Bound",
            up_wave_prob_pct=None,
            down_wave_prob_pct=None,
            range_prob_pct=None,
            expected_wave_pct=None,
            q15_pct=None,
            q85_pct=None,
            signal_hit_rate_pct=None,
            signal_samples=0,
            high_conviction_hit_rate_pct=None,
            high_conviction_samples=0,
            mae_pct=None,
        )

    final_clf = _make_clf()
    final_clf.fit(X[:eval_end][full_mask], y_full[full_mask])
    p_down, p_range, p_up = _extract_probs(final_clf, X[n - 1 : n])

    w_full = wave_ret[:eval_end][full_mask]
    up_vals = w_full[y_full[full_mask] == 2]
    dn_vals = w_full[y_full[full_mask] == 0]
    avg_up_pct = float(np.mean(up_vals)) * 100.0 if len(up_vals) > 0 else 2.0
    avg_down_pct = float(np.mean(dn_vals)) * 100.0 if len(dn_vals) > 0 else -2.0
    expected_pct = round(max(-12.0, min(12.0, p_up * avg_up_pct + p_down * avg_down_pct)), 2)

    if p_up >= 0.45 and (p_up - p_down) >= 0.12:
        wave_state = "Bullish Wave"
    elif p_down >= 0.45 and (p_down - p_up) >= 0.12:
        wave_state = "Bearish Wave"
    else:
        wave_state = "Range-Bound"

    # Empirical P15 / P85 wave range from historical wave returns
    q15_pct = round(float(np.percentile(w_full, 15)) * 100.0, 2)
    q85_pct = round(float(np.percentile(w_full, 85)) * 100.0, 2)

    return WaveForecastResult(
        wave_state=wave_state,
        up_wave_prob_pct=round(p_up * 100.0, 1),
        down_wave_prob_pct=round(p_down * 100.0, 1),
        range_prob_pct=round(p_range * 100.0, 1),
        expected_wave_pct=expected_pct,
        q15_pct=q15_pct,
        q85_pct=q85_pct,
        signal_hit_rate_pct=round((sig_hits / sig_total) * 100.0, 1) if sig_total >= 10 else None,
        signal_samples=sig_total,
        high_conviction_hit_rate_pct=round((hc_hits / hc_total) * 100.0, 1) if hc_total >= 8 else None,
        high_conviction_samples=hc_total,
        mae_pct=round(sum(abs_errs) / len(abs_errs), 2) if abs_errs else None,
    )

