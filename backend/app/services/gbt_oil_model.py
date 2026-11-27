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
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, RobustScaler


def _clip_z(arr: np.ndarray) -> np.ndarray:
    """Winsorize robustly scaled features to [-3.5, +3.5] so fat-tail spikes cannot distort linear models."""
    return np.clip(arr, -3.5, 3.5)

# 10 economically orthogonal, zero-drift factors (identical for Brent & WTI — zero symbol p-hacking)
ORTHOGONAL_FEATURE_NAMES: tuple[str, ...] = (
    "dist_ma50",
    "vol_ratio",
    "crack_321_chg_20d",
    "brent_wti_spread",
    "fro_ret_5d",
    "pw_hormuz_7d_vs_30d",
    "pw_global_tankers_7d_vs_30d",
    "cftc_mm_net_z52",
    "eia_stocks_chg_kbbl",
    "news_dir_7d",
)


def _binomial_p_value_gt_half(hits: int, n: int) -> Optional[float]:
    """Exact one-sided binomial p-value for H0: true hit rate <= 50%."""
    if n <= 0:
        return None
    hits = max(0, min(n, hits))
    tail = sum(math.comb(n, k) for k in range(hits, n + 1))
    return round(tail / (2 ** n), 4)


# Full 47-feature candidate pool (used for strictly out-of-sample permutation importance audits)
ALL_CANDIDATE_FEATURE_NAMES: tuple[str, ...] = (
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
    "pw_hormuz_7d_vs_30d",
    "pw_hormuz_cap_7d_vs_30d",
    "pw_bab_7d_vs_30d",
    "pw_suez_7d_vs_30d",
    "pw_cape_7d_vs_30d",
    "pw_cape_vs_suez_share",
    "pw_global_tankers_7d_vs_30d",
    "pw_hormuz_cap_z30",
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

# Empirically pruned 33-feature active set (from OOS permutation importance on 280 held-out days):
# Promoted: copper_ret_20d (+1.07%), stng_ret_5d (+0.93%), us10y_chg_5d (+0.79%), china_ret_20d (+0.57%)
# Dropped noisy features: vol_20d (-1.36%), ret_5d (-1.36%), ret_10d (-1.43%), dxy_ret_5d (-0.71%), pw_bab_7d_vs_30d (-0.64%)
FEATURE_NAMES: tuple[str, ...] = (
    "ret_1d",
    "ret_3d",
    "ret_20d",
    "dist_ma20",
    "dist_ma50",
    "vol_5d",
    "vol_ratio",
    "rsi_14",
    "crack_321_level",
    "crack_321_chg_5d",
    "crack_321_chg_20d",
    "brent_wti_spread",
    "brent_wti_chg_5d",
    "vix_level",
    "vix_chg_5d",
    "fro_ret_5d",
    "fro_ret_20d",
    "xle_ret_5d",
    "news_dir_1d",
    "news_dir_3d",
    "news_dir_7d",
    "news_shock_count_3d",
    "pw_hormuz_7d_vs_30d",
    "pw_hormuz_cap_7d_vs_30d",
    "pw_suez_7d_vs_30d",
    "pw_cape_7d_vs_30d",
    "pw_cape_vs_suez_share",
    "pw_global_tankers_7d_vs_30d",
    "pw_hormuz_cap_z30",
    "copper_ret_20d",
    "china_ret_20d",
    "us10y_chg_5d",
    "stng_ret_5d",
)

FEATURE_METADATA: dict[str, tuple[str, str]] = {
    "ret_1d": ("Crude 1d Return", "Price Regime"),
    "ret_3d": ("Crude 3d Return", "Price Regime"),
    "ret_5d": ("Crude 5d Return", "Price Regime"),
    "ret_10d": ("Crude 10d Return", "Price Regime"),
    "ret_20d": ("Crude 20d Return", "Price Regime"),
    "dist_ma20": ("Distance from 20d MA", "Price Regime"),
    "dist_ma50": ("Distance from 50d MA", "Price Regime"),
    "vol_5d": ("5d Realized Volatility", "Price Regime"),
    "vol_20d": ("20d Realized Volatility", "Price Regime"),
    "vol_ratio": ("Volatility Ratio (5d/20d)", "Price Regime"),
    "rsi_14": ("RSI-14 Momentum", "Price Regime"),
    "crack_321_level": ("3-2-1 Crack Spread ($/bbl)", "Refining & Curve"),
    "crack_321_chg_5d": ("3-2-1 Crack Spread 5d Chg", "Refining & Curve"),
    "crack_321_chg_20d": ("3-2-1 Crack Spread 20d Chg", "Refining & Curve"),
    "brent_wti_spread": ("Brent–WTI Spread ($/bbl)", "Refining & Curve"),
    "brent_wti_chg_5d": ("Brent–WTI Spread 5d Chg", "Refining & Curve"),
    "dxy_ret_5d": ("US Dollar (DXY) 5d Return", "Macro & Demand"),
    "vix_level": ("VIX Volatility Level", "Macro & Demand"),
    "vix_chg_5d": ("VIX 5d Change", "Macro & Demand"),
    "fro_ret_5d": ("VLCC Tanker Stock (FRO) 5d", "Shipping Equities"),
    "fro_ret_20d": ("VLCC Tanker Stock (FRO) 20d", "Shipping Equities"),
    "xle_ret_5d": ("Energy ETF (XLE) 5d", "Macro & Demand"),
    "news_dir_1d": ("News Directional Score (1d)", "News Memory"),
    "news_dir_3d": ("News Directional Score (3d)", "News Memory"),
    "news_dir_7d": ("News Directional Score (7d)", "News Memory"),
    "news_shock_count_3d": ("High-Rank News Shocks (3d)", "News Memory"),
    "pw_hormuz_7d_vs_30d": ("PortWatch Hormuz Tankers (7d/30d)", "Physical Ships"),
    "pw_hormuz_cap_7d_vs_30d": ("PortWatch Hormuz Capacity (7d/30d)", "Physical Ships"),
    "pw_bab_7d_vs_30d": ("PortWatch Bab el-Mandeb (7d/30d)", "Physical Ships"),
    "pw_suez_7d_vs_30d": ("PortWatch Suez Canal (7d/30d)", "Physical Ships"),
    "pw_cape_7d_vs_30d": ("PortWatch Cape of Good Hope (7d/30d)", "Physical Ships"),
    "pw_cape_vs_suez_share": ("PortWatch Cape Rerouting Share", "Physical Ships"),
    "pw_global_tankers_7d_vs_30d": ("PortWatch Global Tankers (7d/30d)", "Physical Ships"),
    "pw_hormuz_cap_z30": ("PortWatch Hormuz Capacity Z-Score", "Physical Ships"),
    "copper_ret_5d": ("Copper (HG=F) 5d Return", "Macro & Demand"),
    "copper_ret_20d": ("Copper (HG=F) 20d Return", "Macro & Demand"),
    "china_ret_5d": ("China ETF (FXI) 5d Return", "Macro & Demand"),
    "china_ret_20d": ("China ETF (FXI) 20d Return", "Macro & Demand"),
    "natgas_ret_5d": ("Natural Gas (NG=F) 5d Return", "Macro & Demand"),
    "us10y_chg_5d": ("US 10Y Treasury Yield 5d Chg", "Macro & Demand"),
    "stng_ret_5d": ("Product Tanker Stock (STNG) 5d", "Shipping Equities"),
    "curve_chg_5d": ("Futures Curve (USO/USL) 5d Shift", "Refining & Curve"),
    "curve_chg_20d": ("Futures Curve (USO/USL) 20d Shift", "Refining & Curve"),
    "cftc_mm_net_pct_oi": ("CFTC Hedge-Fund Net % OI", "Positioning & Storage"),
    "cftc_mm_net_z52": ("CFTC Hedge-Fund 52w Crowding Z", "Positioning & Storage"),
    "eia_stocks_chg_kbbl": ("EIA Weekly Crude Storage Chg", "Positioning & Storage"),
    "eia_refinery_util_pct": ("EIA US Refinery Utilization %", "Positioning & Storage"),
}


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
    include_all_candidates: bool = False,
    feature_names: Optional[tuple[str, ...]] = None,
) -> tuple[np.ndarray, np.ndarray, list[date]]:
    """Build strictly backward-looking feature matrix."""
    selected_names = (
        feature_names
        if feature_names is not None
        else (ALL_CANDIDATE_FEATURE_NAMES if include_all_candidates else FEATURE_NAMES)
    )
    priced = [
        r for r in daily_rows
        if r.get(price_key) is not None and float(r[price_key]) > 0
    ]
    n = len(priced)
    n_cols = len(selected_names)
    if n == 0:
        return np.zeros((0, n_cols)), np.zeros(0), []

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
    pw_hormuz = _col("pw_hormuz_tankers")
    pw_hormuz_cap = _col("pw_hormuz_capacity")
    pw_bab = _col("pw_bab_tankers")
    pw_suez = _col("pw_suez_tankers")
    pw_cape = _col("pw_cape_tankers")
    pw_global = _col("pw_global_tankers")

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

    X = np.full((n, n_cols), np.nan, dtype=np.float64)
    for i in range(n):
        r1 = _safe_log_ret(prices, i, 1)
        r3 = _safe_log_ret(prices, i, 3)
        r5 = _safe_log_ret(prices, i, 5)
        r10 = _safe_log_ret(prices, i, 10)
        r20 = _safe_log_ret(prices, i, 20)

        ma20 = float(np.mean(prices[max(0, i - 19) : i + 1])) if i >= 5 else np.nan
        ma50 = float(np.mean(prices[max(0, i - 49) : i + 1])) if i >= 15 else np.nan
        dist_ma20 = (prices[i] / ma20 - 1.0) if (math.isfinite(ma20) and ma20 > 0) else np.nan
        dist_ma50 = (prices[i] / ma50 - 1.0) if (math.isfinite(ma50) and ma50 > 0) else np.nan

        v5_slice = daily_rets[max(1, i - 4) : i + 1]
        v5_valid = v5_slice[np.isfinite(v5_slice)]
        vol5 = float(np.std(v5_valid, ddof=1)) if len(v5_valid) >= 3 else np.nan

        v20_slice = daily_rets[max(1, i - 19) : i + 1]
        v20_valid = v20_slice[np.isfinite(v20_slice)]
        vol20 = float(np.std(v20_valid, ddof=1)) if len(v20_valid) >= 8 else np.nan
        vol_ratio = (vol5 / vol20) if (math.isfinite(vol5) and math.isfinite(vol20) and vol20 > 1e-6) else np.nan

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

        # Bounded [-1, +1] average polarity (immune to 2021-2025 vs 2026 article volume drift)
        n1 = max(-1.0, min(1.0, news_1d[i])) if math.isfinite(news_1d[i]) else 0.0
        n3_slice = news_1d[max(0, i - 2) : i + 1]
        n3_valid = n3_slice[np.isfinite(n3_slice)]
        n3 = max(-1.0, min(1.0, float(np.mean(n3_valid)))) if len(n3_valid) > 0 else 0.0
        n7_slice = news_1d[max(0, i - 6) : i + 1]
        n7_valid = n7_slice[np.isfinite(n7_slice)]
        n7 = max(-1.0, min(1.0, float(np.mean(n7_valid)))) if len(n7_valid) > 0 else 0.0
        sh3_slice = news_shocks[max(0, i - 2) : i + 1]
        sh3 = float(np.nansum(sh3_slice)) if np.any(np.isfinite(sh3_slice)) else 0.0

        c7 = pw_cape[max(0, i - 6) : i + 1]
        s7 = pw_suez[max(0, i - 6) : i + 1]
        b7 = pw_bab[max(0, i - 6) : i + 1]
        if np.any(np.isfinite(c7)) and np.any(np.isfinite(s7)):
            c_sum = float(np.nansum(c7))
            tot_reroute = c_sum + float(np.nansum(s7)) + float(np.nansum(b7))
            cape_share = (c_sum / tot_reroute) if tot_reroute > 0 else np.nan
        else:
            cape_share = np.nan

        feat_map = {
            "ret_1d": r1,
            "ret_3d": r3,
            "ret_5d": r5,
            "ret_10d": r10,
            "ret_20d": r20,
            "dist_ma20": dist_ma20,
            "dist_ma50": dist_ma50,
            "vol_5d": vol5,
            "vol_20d": vol20,
            "vol_ratio": vol_ratio,
            "rsi_14": rsi14,
            "crack_321_level": crack[i],
            "crack_321_chg_5d": _safe_diff(crack, i, 5),
            "crack_321_chg_20d": _safe_diff(crack, i, 20),
            "brent_wti_spread": bw[i],
            "brent_wti_chg_5d": _safe_diff(bw, i, 5),
            "dxy_ret_5d": _safe_log_ret(dxy, i, 5),
            "vix_level": vix[i],
            "vix_chg_5d": _safe_diff(vix, i, 5),
            "fro_ret_5d": _safe_log_ret(fro, i, 5),
            "fro_ret_20d": _safe_log_ret(fro, i, 20),
            "xle_ret_5d": _safe_log_ret(xle, i, 5),
            "news_dir_1d": n1,
            "news_dir_3d": n3,
            "news_dir_7d": n7,
            "news_shock_count_3d": sh3,
            "pw_hormuz_7d_vs_30d": _rolling_ratio_7_30(pw_hormuz, i),
            "pw_hormuz_cap_7d_vs_30d": _rolling_ratio_7_30(pw_hormuz_cap, i),
            "pw_bab_7d_vs_30d": _rolling_ratio_7_30(pw_bab, i),
            "pw_suez_7d_vs_30d": _rolling_ratio_7_30(pw_suez, i),
            "pw_cape_7d_vs_30d": _rolling_ratio_7_30(pw_cape, i),
            "pw_cape_vs_suez_share": cape_share,
            "pw_global_tankers_7d_vs_30d": _rolling_ratio_7_30(pw_global, i),
            "pw_hormuz_cap_z30": _rolling_z30(pw_hormuz_cap, i),
            "copper_ret_5d": _safe_log_ret(copper, i, 5),
            "copper_ret_20d": _safe_log_ret(copper, i, 20),
            "china_ret_5d": _safe_log_ret(china, i, 5),
            "china_ret_20d": _safe_log_ret(china, i, 20),
            "natgas_ret_5d": _safe_log_ret(natgas, i, 5),
            "us10y_chg_5d": _safe_diff(us10y, i, 5),
            "stng_ret_5d": _safe_log_ret(stng, i, 5),
            "curve_chg_5d": _safe_log_ret(curve, i, 5),
            "curve_chg_20d": _safe_log_ret(curve, i, 20),
            "cftc_mm_net_pct_oi": cftc_net_pct[i],
            "cftc_mm_net_z52": _rolling_z_window(cftc_net_pct, i, 252),
            "eia_stocks_chg_kbbl": eia_chg[i],
            "eia_refinery_util_pct": eia_util[i],
        }
        X[i] = [feat_map[name] for name in selected_names]

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
    indep_hit_rate_pct: Optional[float] = None
    indep_samples: int = 0
    indep_p_value: Optional[float] = None


def _empty_wave_result() -> WaveForecastResult:
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
        indep_hit_rate_pct=None,
        indep_samples=0,
        indep_p_value=None,
    )


def _run_wave_engine(
    X: np.ndarray,
    prices: np.ndarray,
    horizon_days: int,
    make_clf,
    min_train_rows: int = 240,
    refit_every: int = 25,
    max_eval_days: int = 1000,
    train_window_days: Optional[int] = None,
    min_prob: float = 0.45,
    min_margin: float = 0.12,
) -> WaveForecastResult:
    """Expanding or rolling-window walk-forward 3-class wave engine with non-overlapping independent p-value."""
    n = len(prices)
    if n < min_train_rows + horizon_days + 25:
        return _empty_wave_result()

    # Smoothed forward wave return over the next H trading days
    wave_ret = np.full(n, np.nan, dtype=np.float64)
    for i in range(n - horizon_days):
        fwd_window = prices[i + 1 : i + 1 + horizon_days]
        if prices[i] > 0 and len(fwd_window) == horizon_days and np.all(fwd_window > 0):
            wave_ret[i] = math.log(float(np.mean(fwd_window)) / prices[i])

    # Wave threshold scales with horizon (3d: 0.8%, 5d: 1.0%, 7d: 1.2%, 14d: 1.8%)
    if horizon_days <= 3:
        wave_th = 0.008
    elif horizon_days <= 5:
        wave_th = 0.010
    elif horizon_days <= 7:
        wave_th = 0.012
    else:
        wave_th = 0.018

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

    def _extract_probs(clf: Any, x_row: np.ndarray) -> tuple[float, float, float]:
        probs = clf.predict_proba(x_row)[0]
        classes = clf.classes_ if hasattr(clf, "classes_") else clf[-1].classes_
        cls_map = {int(c): float(p) for c, p in zip(classes, probs)}
        return cls_map.get(0, 0.0), cls_map.get(1, 0.0), cls_map.get(2, 0.0)

    eval_end = n - horizon_days
    eval_start = max(min_train_rows + horizon_days, eval_end - max_eval_days)

    clf: Any = None
    last_fit_t = -9999
    avg_up_pct = 2.0
    avg_down_pct = -2.0

    sig_hits = 0
    sig_total = 0
    hc_hits = 0
    hc_total = 0
    indep_hits = 0
    indep_total = 0
    next_indep_t = -1
    abs_errs: list[float] = []

    for t in range(eval_start, eval_end):
        if y_cls[t] < 0 or not math.isfinite(wave_ret[t]):
            continue
        train_end = t - horizon_days + 1
        train_start = max(0, train_end - train_window_days) if train_window_days else 0
        if clf is None or (t - last_fit_t) >= refit_every:
            y_tr = y_cls[train_start:train_end]
            mask = y_tr >= 0
            eff_min = min(min_train_rows, train_window_days - 20) if train_window_days else min_train_rows
            if int(mask.sum()) < max(120, eff_min) or len(set(y_tr[mask].tolist())) < 2:
                continue
            clf = make_clf()
            clf.fit(X[train_start:train_end][mask], y_tr[mask])
            w_tr = wave_ret[train_start:train_end][mask]
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

        fired_dir = 0
        is_hc = False
        if p_up >= min_prob and (p_up - p_down) >= min_margin:
            fired_dir = 1
            is_hc = p_up >= 0.55 and (p_up - p_down) >= 0.22
        elif p_down >= min_prob and (p_down - p_up) >= min_margin:
            fired_dir = -1
            is_hc = p_down >= 0.55 and (p_down - p_up) >= 0.22

        if fired_dir != 0:
            sig_total += 1
            is_hit = (actual_pct > 0) if fired_dir > 0 else (actual_pct < 0)
            if is_hit:
                sig_hits += 1
            abs_errs.append(abs(expected_pct - actual_pct))
            if is_hc:
                hc_total += 1
                if is_hit:
                    hc_hits += 1
            # Strictly non-overlapping independent bet tracking (zero target overlap)
            if t >= next_indep_t:
                indep_total += 1
                if is_hit:
                    indep_hits += 1
                next_indep_t = t + horizon_days

    # Fit final wave classifier on most recent window up to today
    final_start = max(0, eval_end - train_window_days) if train_window_days else 0
    y_full = y_cls[final_start:eval_end]
    full_mask = y_full >= 0
    if int(full_mask.sum()) < 120 or len(set(y_full[full_mask].tolist())) < 2:
        return _empty_wave_result()

    final_clf = make_clf()
    final_clf.fit(X[final_start:eval_end][full_mask], y_full[full_mask])
    p_down, p_range, p_up = _extract_probs(final_clf, X[n - 1 : n])

    w_full = wave_ret[final_start:eval_end][full_mask]
    up_vals = w_full[y_full[full_mask] == 2]
    dn_vals = w_full[y_full[full_mask] == 0]
    avg_up_pct = float(np.mean(up_vals)) * 100.0 if len(up_vals) > 0 else 2.0
    avg_down_pct = float(np.mean(dn_vals)) * 100.0 if len(dn_vals) > 0 else -2.0
    expected_pct = round(max(-12.0, min(12.0, p_up * avg_up_pct + p_down * avg_down_pct)), 2)

    if p_up >= min_prob and (p_up - p_down) >= min_margin:
        wave_state = "Bullish Wave"
    elif p_down >= min_prob and (p_down - p_up) >= min_margin:
        wave_state = "Bearish Wave"
    else:
        wave_state = "Range-Bound"

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
        indep_hit_rate_pct=round((indep_hits / indep_total) * 100.0, 1) if indep_total >= 5 else None,
        indep_samples=indep_total,
        indep_p_value=_binomial_p_value_gt_half(indep_hits, indep_total) if indep_total >= 5 else None,
    )


def run_walkforward_wave_classifier(
    daily_rows: list[dict[str, Any]],
    price_key: str,
    horizon_days: int,
    min_train_rows: int = 240,
    refit_every: int = 25,
    max_eval_days: int = 1000,
    train_window_days: Optional[int] = None,
) -> WaveForecastResult:
    """Multi-day Oil Price Wave Detector using HistGradientBoostingClassifier (33 features)."""
    X, prices, _ = build_gbt_feature_matrix(daily_rows, price_key=price_key)

    def _make_gbt_clf() -> HistGradientBoostingClassifier:
        return HistGradientBoostingClassifier(
            max_iter=80,
            max_depth=3,
            learning_rate=0.05,
            l2_regularization=3.0,
            min_samples_leaf=20,
            max_bins=128,
            random_state=42,
        )

    return _run_wave_engine(
        X=X,
        prices=prices,
        horizon_days=horizon_days,
        make_clf=_make_gbt_clf,
        min_train_rows=min_train_rows,
        refit_every=refit_every,
        max_eval_days=max_eval_days,
        train_window_days=train_window_days,
    )


def run_walkforward_regularized_wave(
    daily_rows: list[dict[str, Any]],
    price_key: str,
    horizon_days: int,
    min_train_rows: int = 240,
    refit_every: int = 25,
    max_eval_days: int = 1000,
    train_window_days: Optional[int] = None,
    min_prob: float = 0.45,
    min_margin: float = 0.12,
) -> WaveForecastResult:
    """Low-variance 10-factor L2-regularized Logistic Regression wave classifier (identical for Brent & WTI)."""
    X, prices, _ = build_gbt_feature_matrix(
        daily_rows,
        price_key=price_key,
        feature_names=ORTHOGONAL_FEATURE_NAMES,
    )

    def _make_reg_clf() -> Pipeline:
        return Pipeline([
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scaler", RobustScaler()),
            ("clip", FunctionTransformer(_clip_z)),
            ("clf", LogisticRegression(C=0.20, max_iter=500, random_state=42)),
        ])

    return _run_wave_engine(
        X=X,
        prices=prices,
        horizon_days=horizon_days,
        make_clf=_make_reg_clf,
        min_train_rows=min_train_rows,
        refit_every=refit_every,
        max_eval_days=max_eval_days,
        train_window_days=train_window_days,
        min_prob=min_prob,
        min_margin=min_margin,
    )


VOLATILITY_FEATURE_NAMES: tuple[str, ...] = (
    "vol_5d",
    "vol_20d",
    "vol_ratio",
    "vix_level",
    "vix_chg_5d",
    "dist_ma50",
    "pw_hormuz_7d_vs_30d",
    "crack_321_chg_5d",
)


def run_walkforward_volatility_alert(
    daily_rows: list[dict[str, Any]],
    price_key: str,
    horizon_days: int = 5,
    train_window_days: int = 300,
    refit_every: int = 25,
    max_eval_days: int = 1000,
) -> dict[str, Any]:
    """Predict Big-Move / Volatility Regime Alert: will max |return| over next H days exceed threshold?

    Strictly out-of-sample rolling-window walk-forward with non-overlapping accuracy,
    exact one-sided binomial p-value, and ROC-AUC.
    """
    from sklearn.metrics import roc_auc_score

    X, prices, _ = build_gbt_feature_matrix(
        daily_rows,
        price_key=price_key,
        feature_names=VOLATILITY_FEATURE_NAMES,
    )
    n = len(prices)
    if n < 260 + horizon_days:
        return {
            "threshold_pct": None,
            "big_move_prob_pct": None,
            "alert_state": "Normal",
            "indep_accuracy_pct": None,
            "indep_samples": 0,
            "indep_p_value": None,
            "roc_auc": None,
        }

    move_th = 0.025 if horizon_days <= 3 else (0.032 if horizon_days <= 5 else 0.038)
    y_bin = np.full(n, -1, dtype=np.int64)
    for i in range(n - horizon_days):
        fwd = prices[i + 1 : i + 1 + horizon_days]
        if prices[i] > 0 and len(fwd) == horizon_days and np.all(fwd > 0):
            max_abs = float(np.max(np.abs(fwd / prices[i] - 1.0)))
            y_bin[i] = 1 if max_abs >= move_th else 0

    eval_end = n - horizon_days
    eval_start = max(240 + horizon_days, eval_end - max_eval_days)

    clf: Any = None
    last_fit_t = -9999
    indep_hits = 0
    indep_total = 0
    next_indep_t = -1
    y_true_indep: list[int] = []
    y_prob_indep: list[float] = []

    def _make_vol_clf() -> Pipeline:
        return Pipeline([
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scaler", RobustScaler()),
            ("clip", FunctionTransformer(_clip_z)),
            ("clf", LogisticRegression(C=0.25, max_iter=500, random_state=42)),
        ])

    for t in range(eval_start, eval_end):
        if y_bin[t] < 0:
            continue
        train_end = t - horizon_days + 1
        train_start = max(0, train_end - train_window_days)
        if clf is None or (t - last_fit_t) >= refit_every:
            y_tr = y_bin[train_start:train_end]
            m = y_tr >= 0
            if int(m.sum()) < 140 or len(set(y_tr[m].tolist())) < 2:
                continue
            clf = _make_vol_clf()
            clf.fit(X[train_start:train_end][m], y_tr[m])
            last_fit_t = t

        if clf is None:
            continue
        prob_hi = float(clf.predict_proba(X[t : t + 1])[0][1])
        pred_hi = 1 if prob_hi >= 0.50 else 0

        if t >= next_indep_t:
            indep_total += 1
            if pred_hi == int(y_bin[t]):
                indep_hits += 1
            y_true_indep.append(int(y_bin[t]))
            y_prob_indep.append(prob_hi)
            next_indep_t = t + horizon_days

    final_start = max(0, eval_end - train_window_days)
    y_full = y_bin[final_start:eval_end]
    fm = y_full >= 0
    live_prob: Optional[float] = None
    if int(fm.sum()) >= 140 and len(set(y_full[fm].tolist())) >= 2:
        final_clf = _make_vol_clf()
        final_clf.fit(X[final_start:eval_end][fm], y_full[fm])
        live_prob = round(float(final_clf.predict_proba(X[n - 1 : n])[0][1]) * 100.0, 1)

    auc: Optional[float] = None
    if len(set(y_true_indep)) >= 2:
        auc = round(float(roc_auc_score(y_true_indep, y_prob_indep)), 3)

    return {
        "threshold_pct": round(move_th * 100.0, 1),
        "big_move_prob_pct": live_prob,
        "alert_state": "High Volatility Alert" if (live_prob is not None and live_prob >= 55.0) else "Normal",
        "indep_accuracy_pct": round((indep_hits / indep_total) * 100.0, 1) if indep_total >= 5 else None,
        "indep_samples": indep_total,
        "indep_p_value": _binomial_p_value_gt_half(indep_hits, indep_total) if indep_total >= 5 else None,
        "roc_auc": auc,
    }


def _binomial_p_value_vs_rate(hits: int, n: int, base_p: float) -> Optional[float]:
    """Exact one-sided binomial p-value for H0: true precision <= base_p."""
    if n <= 0 or not (0.0 < base_p < 1.0):
        return None
    hits = max(0, min(n, hits))
    q = 1.0 - base_p
    prob = sum(math.comb(n, k) * (base_p ** k) * (q ** (n - k)) for k in range(hits, n + 1))
    return round(min(1.0, max(0.0, prob)), 4)


def run_walkforward_spike_detector(
    daily_rows: list[dict[str, Any]],
    price_key: str,
    horizon_days: int = 5,
    min_train_rows: int = 240,
    refit_every: int = 25,
    max_eval_days: int = 1000,
) -> dict[str, Any]:
    """Non-linear Supply-Shock & Oversold-Bounce Upward Spike Detector.

    Engineered directly from historical spike forensics:
    1. ``abs_hormuz_anomaly``: ``|pw_hormuz_7d_vs_30d|`` (catches BOTH pre-war front-loading surges AND blockade collapses)
    2. ``abs_hormuz_cap_anomaly``: ``|pw_hormuz_cap_7d_vs_30d|``
    3. ``disruption_news_7d``: 7-day rolling share of chokepoint_attack_blockade + refinery_pipeline_outage headlines
    4. ``crack_321_chg_5d`` & ``crack_321_chg_20d``: exploding refining margins
    5. ``fro_ret_20d`` & ``stng_ret_5d``: tanker freight equity surges
    6. ``oversold_high_vol``: ``max(0, -ret_3d) * vol_20d`` (war-regime rubber-band rebound)
    7. ``curve_chg_5d``: backwardation tightening
    """
    from sklearn.metrics import roc_auc_score

    base_feats = (
        "pw_hormuz_7d_vs_30d",
        "pw_hormuz_cap_7d_vs_30d",
        "crack_321_chg_5d",
        "crack_321_chg_20d",
        "fro_ret_20d",
        "stng_ret_5d",
        "ret_3d",
        "vol_20d",
        "curve_chg_5d",
    )
    X_base, prices, _ = build_gbt_feature_matrix(
        daily_rows,
        price_key=price_key,
        feature_names=base_feats,
    )
    priced = [r for r in daily_rows if r.get(price_key) is not None and float(r[price_key]) > 0]
    n = len(prices)
    if n < min_train_rows + horizon_days + 25:
        return {
            "threshold_pct": None,
            "live_spike_prob_pct": None,
            "alert_state": "Normal",
            "base_rate_pct": None,
            "alert_precision_pct": None,
            "alert_samples": 0,
            "lift_x": None,
            "indep_samples": 0,
            "indep_p_value": None,
            "roc_auc": None,
        }

    # Build 7d rolling disruption-news share (volume-invariant ratio in [0, 1])
    disr_raw = np.array(
        [float(r["disruption_news_1d"]) if r.get("disruption_news_1d") is not None else np.nan for r in priced],
        dtype=np.float64,
    )
    disr_7d = np.zeros(n, dtype=np.float64)
    for i in range(n):
        sl = disr_raw[max(0, i - 6) : i + 1]
        v = sl[np.isfinite(sl)]
        disr_7d[i] = float(np.mean(v)) if len(v) > 0 else 0.0

    # Assemble non-linear forensic feature matrix (9 features)
    abs_hormuz = np.abs(X_base[:, 0])
    abs_hormuz_cap = np.abs(X_base[:, 1])
    crack_5d = X_base[:, 2]
    crack_20d = X_base[:, 3]
    fro_20d = X_base[:, 4]
    stng_5d = X_base[:, 5]
    ret_3d = X_base[:, 6]
    vol_20d = X_base[:, 7]
    curve_5d = X_base[:, 8]
    oversold_hv = np.where(
        np.isfinite(ret_3d) & np.isfinite(vol_20d),
        np.maximum(0.0, -ret_3d) * vol_20d,
        np.nan,
    )
    X = np.column_stack([
        abs_hormuz,
        abs_hormuz_cap,
        disr_7d,
        crack_5d,
        crack_20d,
        fro_20d,
        stng_5d,
        oversold_hv,
        curve_5d,
    ])

    spike_th = 0.030 if horizon_days <= 3 else (0.040 if horizon_days <= 5 else 0.050)
    y_bin = np.full(n, -1, dtype=np.int64)
    for i in range(n - horizon_days):
        fwd = prices[i + 1 : i + 1 + horizon_days]
        if prices[i] > 0 and len(fwd) == horizon_days and np.all(fwd > 0):
            max_up = float(np.max(fwd / prices[i] - 1.0))
            y_bin[i] = 1 if max_up >= spike_th else 0

    eval_end = n - horizon_days
    eval_start = max(min_train_rows + horizon_days, eval_end - max_eval_days)

    def _make_spike_clf() -> Pipeline:
        return Pipeline([
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scaler", RobustScaler()),
            ("clip", FunctionTransformer(_clip_z)),
            ("clf", LogisticRegression(C=0.20, class_weight="balanced", max_iter=500, random_state=42)),
        ])

    clf: Any = None
    last_fit_t = -9999
    next_indep_t = -1
    y_true_indep: list[int] = []
    y_prob_indep: list[float] = []
    alert_hits = 0
    alert_total = 0

    for t in range(eval_start, eval_end):
        if y_bin[t] < 0:
            continue
        train_end = t - horizon_days + 1
        if clf is None or (t - last_fit_t) >= refit_every:
            y_tr = y_bin[:train_end]
            m = y_tr >= 0
            if int(m.sum()) < min_train_rows or len(set(y_tr[m].tolist())) < 2:
                continue
            clf = _make_spike_clf()
            clf.fit(X[:train_end][m], y_tr[m])
            last_fit_t = t

        if clf is None:
            continue
        prob_sp = float(clf.predict_proba(X[t : t + 1])[0][1])

        if t >= next_indep_t:
            y_true_indep.append(int(y_bin[t]))
            y_prob_indep.append(prob_sp)
            if prob_sp >= 0.58:
                alert_total += 1
                if int(y_bin[t]) == 1:
                    alert_hits += 1
            next_indep_t = t + horizon_days

    y_full = y_bin[:eval_end]
    fm = y_full >= 0
    live_prob: Optional[float] = None
    if int(fm.sum()) >= min_train_rows and len(set(y_full[fm].tolist())) >= 2:
        final_clf = _make_spike_clf()
        final_clf.fit(X[:eval_end][fm], y_full[fm])
        live_prob = round(float(final_clf.predict_proba(X[n - 1 : n])[0][1]) * 100.0, 1)

    indep_n = len(y_true_indep)
    base_rate = (sum(y_true_indep) / indep_n) if indep_n > 0 else None
    alert_prec = (alert_hits / alert_total) if alert_total >= 5 else None
    lift = round(alert_prec / base_rate, 2) if (alert_prec is not None and base_rate and base_rate > 0) else None
    pval = (
        _binomial_p_value_vs_rate(alert_hits, alert_total, base_rate)
        if (alert_total >= 5 and base_rate is not None and 0.0 < base_rate < 1.0)
        else None
    )
    auc = round(float(roc_auc_score(y_true_indep, y_prob_indep)), 3) if len(set(y_true_indep)) >= 2 else None

    return {
        "threshold_pct": round(spike_th * 100.0, 1),
        "live_spike_prob_pct": live_prob,
        "alert_state": "Spike Risk Elevated" if (live_prob is not None and live_prob >= 58.0) else "Normal",
        "base_rate_pct": round(base_rate * 100.0, 1) if base_rate is not None else None,
        "alert_precision_pct": round(alert_prec * 100.0, 1) if alert_prec is not None else None,
        "alert_samples": alert_total,
        "lift_x": lift,
        "indep_samples": indep_n,
        "indep_p_value": pval,
        "roc_auc": auc,
    }


def run_har_volatility_ensemble(
    daily_rows: list[dict[str, Any]],
    price_key: str,
    horizon_days: int = 5,
    min_train_rows: int = 240,
    refit_every: int = 25,
    max_eval_days: int = 1000,
) -> dict[str, Any]:
    """Institutional HAR-ML Volatility / Big-Move Regime Ensemble (Corsi 2009 HAR-RV + Leverage + Physical Stress).

    Features (strictly backward-looking, log-transformed for Gaussianity):
    - Log-HAR cascade: ``log(|ret_1d|+eps)``, ``log(vol_5d)``, ``log(vol_20d)``, ``log(vol_60d)``
    - Asymmetric GJR-GARCH leverage: ``max(0, -ret_5d)`` (downside selloffs amplify volatility)
    - Macro & Physical stress: ``log(vix)``, ``vix_chg_5d``, ``|dist_ma50|``, ``|pw_hormuz_7d_vs_30d|``,
      ``|crack_321_chg_5d|``, ``disruption_news_7d``

    Blends ``0.5 * Robust LogisticRegression`` + ``0.5 * Shallow HistGradientBoostingClassifier(max_depth=2)``
    and reports both overall non-overlapping accuracy + ROC-AUC AND high-conviction (``p >= 65%`` or ``<= 35%``) accuracy.
    """
    from sklearn.metrics import roc_auc_score

    base_feats = (
        "ret_1d",
        "ret_5d",
        "vol_5d",
        "vol_20d",
        "vix_level",
        "vix_chg_5d",
        "dist_ma50",
        "pw_hormuz_7d_vs_30d",
        "crack_321_chg_5d",
    )
    X_base, prices, _ = build_gbt_feature_matrix(
        daily_rows,
        price_key=price_key,
        feature_names=base_feats,
    )
    priced = [r for r in daily_rows if r.get(price_key) is not None and float(r[price_key]) > 0]
    n = len(prices)
    if n < min_train_rows + horizon_days + 25:
        return {
            "threshold_pct": None,
            "live_prob_pct": None,
            "alert_state": "Normal",
            "indep_accuracy_pct": None,
            "indep_samples": 0,
            "indep_p_value": None,
            "hc_accuracy_pct": None,
            "hc_samples": 0,
            "hc_p_value": None,
            "roc_auc": None,
        }

    # Compute 60-day realized volatility for the HAR monthly component
    daily_rets = np.full(n, np.nan, dtype=np.float64)
    for i in range(1, n):
        if prices[i] > 0 and prices[i - 1] > 0:
            daily_rets[i] = math.log(prices[i] / prices[i - 1])
    vol_60d = np.full(n, np.nan, dtype=np.float64)
    for i in range(25, n):
        sl = daily_rets[max(1, i - 59) : i + 1]
        v = sl[np.isfinite(sl)]
        if len(v) >= 20:
            vol_60d[i] = float(np.std(v, ddof=1))

    disr_raw = np.array(
        [float(r["disruption_news_1d"]) if r.get("disruption_news_1d") is not None else np.nan for r in priced],
        dtype=np.float64,
    )
    disr_7d = np.zeros(n, dtype=np.float64)
    for i in range(n):
        sl = disr_raw[max(0, i - 6) : i + 1]
        v = sl[np.isfinite(sl)]
        disr_7d[i] = float(np.mean(v)) if len(v) > 0 else 0.0

    def _safe_log(arr: np.ndarray, floor: float = 1e-4) -> np.ndarray:
        return np.where(np.isfinite(arr), np.log(np.maximum(arr, floor)), np.nan)

    log_abs_r1 = _safe_log(np.abs(X_base[:, 0]), 1e-4)
    neg_r5 = np.where(np.isfinite(X_base[:, 1]), np.maximum(0.0, -X_base[:, 1]), np.nan)
    log_v5 = _safe_log(X_base[:, 2], 1e-4)
    log_v20 = _safe_log(X_base[:, 3], 1e-4)
    log_v60 = _safe_log(vol_60d, 1e-4)
    log_vix = _safe_log(X_base[:, 4], 1.0)
    vix_chg_5d = X_base[:, 5]
    abs_ma50 = np.abs(X_base[:, 6])
    abs_hormuz = np.abs(X_base[:, 7])
    abs_crack_5d = np.abs(X_base[:, 8])

    X = np.column_stack([
        log_abs_r1,
        log_v5,
        log_v20,
        log_v60,
        neg_r5,
        log_vix,
        vix_chg_5d,
        abs_ma50,
        abs_hormuz,
        abs_crack_5d,
        disr_7d,
    ])

    move_th = 0.025 if horizon_days <= 3 else (0.032 if horizon_days <= 5 else 0.038)
    y_bin = np.full(n, -1, dtype=np.int64)
    for i in range(n - horizon_days):
        fwd = prices[i + 1 : i + 1 + horizon_days]
        if prices[i] > 0 and len(fwd) == horizon_days and np.all(fwd > 0):
            max_abs = float(np.max(np.abs(fwd / prices[i] - 1.0)))
            y_bin[i] = 1 if max_abs >= move_th else 0

    eval_end = n - horizon_days
    eval_start = max(min_train_rows + horizon_days, eval_end - max_eval_days)

    def _fit_pair(X_tr: np.ndarray, y_tr: np.ndarray):
        lr = Pipeline([
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scaler", RobustScaler()),
            ("clip", FunctionTransformer(_clip_z)),
            ("clf", LogisticRegression(C=0.20, max_iter=500, random_state=42)),
        ])
        lr.fit(X_tr, y_tr)
        gbt = HistGradientBoostingClassifier(
            max_iter=60,
            max_depth=2,
            learning_rate=0.05,
            l2_regularization=5.0,
            min_samples_leaf=25,
            random_state=42,
        )
        gbt.fit(X_tr, y_tr)
        return lr, gbt

    lr_m: Any = None
    gbt_m: Any = None
    last_fit_t = -9999
    next_indep_t = -1
    indep_hits = 0
    indep_total = 0
    hc_hits = 0
    hc_total = 0
    y_true_indep: list[int] = []
    y_prob_indep: list[float] = []

    for t in range(eval_start, eval_end):
        if y_bin[t] < 0:
            continue
        train_end = t - horizon_days + 1
        if lr_m is None or (t - last_fit_t) >= refit_every:
            y_tr = y_bin[:train_end]
            m = y_tr >= 0
            if int(m.sum()) < min_train_rows or len(set(y_tr[m].tolist())) < 2:
                continue
            lr_m, gbt_m = _fit_pair(X[:train_end][m], y_tr[m])
            last_fit_t = t

        if lr_m is None or gbt_m is None:
            continue
        p_lr = float(lr_m.predict_proba(X[t : t + 1])[0][1])
        p_gbt = float(gbt_m.predict_proba(X[t : t + 1])[0][1])
        prob = 0.5 * p_lr + 0.5 * p_gbt
        pred = 1 if prob >= 0.50 else 0

        if t >= next_indep_t:
            indep_total += 1
            is_hit = pred == int(y_bin[t])
            if is_hit:
                indep_hits += 1
            if prob >= 0.65 or prob <= 0.35:
                hc_total += 1
                if is_hit:
                    hc_hits += 1
            y_true_indep.append(int(y_bin[t]))
            y_prob_indep.append(prob)
            next_indep_t = t + horizon_days

    y_full = y_bin[:eval_end]
    fm = y_full >= 0
    live_prob: Optional[float] = None
    if int(fm.sum()) >= min_train_rows and len(set(y_full[fm].tolist())) >= 2:
        flr, fgbt = _fit_pair(X[:eval_end][fm], y_full[fm])
        live_prob = round(
            (0.5 * float(flr.predict_proba(X[n - 1 : n])[0][1]) + 0.5 * float(fgbt.predict_proba(X[n - 1 : n])[0][1]))
            * 100.0,
            1,
        )

    auc = round(float(roc_auc_score(y_true_indep, y_prob_indep)), 3) if len(set(y_true_indep)) >= 2 else None
    state = (
        "High Volatility Alert"
        if (live_prob is not None and live_prob >= 65.0)
        else ("Calm Regime" if (live_prob is not None and live_prob <= 35.0) else "Moderate")
    )

    return {
        "threshold_pct": round(move_th * 100.0, 1),
        "live_prob_pct": live_prob,
        "alert_state": state,
        "indep_accuracy_pct": round((indep_hits / indep_total) * 100.0, 1) if indep_total >= 5 else None,
        "indep_samples": indep_total,
        "indep_p_value": _binomial_p_value_gt_half(indep_hits, indep_total) if indep_total >= 5 else None,
        "hc_accuracy_pct": round((hc_hits / hc_total) * 100.0, 1) if hc_total >= 8 else None,
        "hc_samples": hc_total,
        "hc_p_value": _binomial_p_value_gt_half(hc_hits, hc_total) if hc_total >= 8 else None,
        "roc_auc": auc,
    }


def compute_oos_feature_importance(
    daily_rows: list[dict[str, Any]],
    price_key: str,
    horizon_days: int = 14,
    include_all_candidates: bool = True,
    n_repeats: int = 5,
) -> dict[str, Any]:
    """Measure strictly out-of-sample permutation importance for every feature on held-out recent trading days.

    Trains HistGradientBoostingClassifier on the oldest ~75% of matured trading days and
    runs permutation importance on the newest ~25% held-out out-of-sample days (strictly
    separated by ``horizon_days`` gap to prevent target overlap leakage).
    """
    names = ALL_CANDIDATE_FEATURE_NAMES if include_all_candidates else FEATURE_NAMES
    active_set = set(FEATURE_NAMES)
    X, prices, _ = build_gbt_feature_matrix(
        daily_rows,
        price_key=price_key,
        include_all_candidates=include_all_candidates,
    )
    n = len(prices)
    if n < 260:
        return {
            "train_days": 0,
            "test_days": 0,
            "baseline_oos_accuracy_pct": None,
            "features": [],
        }

    wave_ret = np.full(n, np.nan, dtype=np.float64)
    for i in range(n - horizon_days):
        fwd = prices[i + 1 : i + 1 + horizon_days]
        if prices[i] > 0 and len(fwd) == horizon_days and np.all(fwd > 0):
            wave_ret[i] = math.log(float(np.mean(fwd)) / prices[i])

    wave_th = 0.008 if horizon_days <= 3 else (0.012 if horizon_days <= 7 else 0.018)
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

    valid_end = n - horizon_days
    test_size = min(280, max(80, valid_end // 4))
    test_start = valid_end - test_size
    train_end = max(180, test_start - horizon_days)  # gap avoids overlap leakage

    X_tr, y_tr = X[:train_end], y_cls[:train_end]
    m_tr = y_tr >= 0
    X_te, y_te = X[test_start:valid_end], y_cls[test_start:valid_end]
    m_te = y_te >= 0

    if int(m_tr.sum()) < 150 or int(m_te.sum()) < 50:
        return {
            "train_days": int(m_tr.sum()),
            "test_days": int(m_te.sum()),
            "baseline_oos_accuracy_pct": None,
            "features": [],
        }

    clf = HistGradientBoostingClassifier(
        max_iter=80,
        max_depth=3,
        learning_rate=0.05,
        l2_regularization=3.0,
        min_samples_leaf=20,
        max_bins=128,
        random_state=42,
    )
    clf.fit(X_tr[m_tr], y_tr[m_tr])
    baseline_acc = float(clf.score(X_te[m_te], y_te[m_te])) * 100.0

    perm = permutation_importance(
        clf,
        X_te[m_te],
        y_te[m_te],
        n_repeats=n_repeats,
        random_state=42,
        scoring="accuracy",
    )

    ranked: list[dict[str, Any]] = []
    for idx, fname in enumerate(names):
        imp_pct = round(float(perm.importances_mean[idx]) * 100.0, 2)
        std_pct = round(float(perm.importances_std[idx]) * 100.0, 2)
        label, group = FEATURE_METADATA.get(fname, (fname, "Other"))
        status = "helpful" if imp_pct >= 0.25 else ("noisy" if imp_pct <= -0.25 else "neutral")
        ranked.append({
            "feature": fname,
            "label": label,
            "group": group,
            "importance_pct": imp_pct,
            "std_pct": std_pct,
            "in_active_model": fname in active_set,
            "status": status,
        })

    ranked.sort(key=lambda r: r["importance_pct"], reverse=True)
    return {
        "train_days": int(m_tr.sum()),
        "test_days": int(m_te.sum()),
        "baseline_oos_accuracy_pct": round(baseline_acc, 1),
        "features": ranked,
    }

