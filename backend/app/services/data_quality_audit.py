"""Data Quality & Integrity Audit Pipeline for Oil Price Forecasting.

Validates raw PostgreSQL tables (oil_prices, oil_macro_daily, oil_fundamentals_weekly,
portwatch_chokepoint_daily, news_events) AND the assembled 47-feature ML matrix for:
1. Completeness (null rates, date span, staleness, cross-table join alignment)
2. Sanity & Outliers (impossible values, >15% single-day jumps, stuck/flatlined series)
3. Temporal Distribution Drift (train-half vs recent-half z-shift to catch source drift)
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chokepoint import PortWatchChokepointDaily
from app.models.global_oil_features import (
    OilFundamentalsWeekly,
    OilMacroDaily,
    OilPrice,
)
from app.services.gbt_oil_model import (
    ALL_CANDIDATE_FEATURE_NAMES,
    FEATURE_METADATA,
    ORTHOGONAL_FEATURE_NAMES,
    build_gbt_feature_matrix,
)
from app.services.news_predictor import build_news_analog_memory
from app.services.oil_price_predictor import _assemble_gbt_daily_rows


def _max_consecutive_equal(values: list[Optional[float]]) -> int:
    """Longest run of identical finite values (detects stuck/flatlined daily feeds)."""
    best = 0
    cur = 0
    prev: Optional[float] = None
    for v in values:
        if v is None or not math.isfinite(v):
            cur = 0
            prev = None
            continue
        if prev is not None and abs(v - prev) <= 1e-9:
            cur += 1
        else:
            cur = 1
            prev = v
        if cur > best:
            best = cur
    return best


def _audit_numeric_series(
    name: str,
    values: list[Optional[float]],
    dates: list[date],
    *,
    min_ok: Optional[float] = None,
    max_ok: Optional[float] = None,
    max_daily_pct_jump: Optional[float] = None,
    check_stuck: bool = True,
) -> dict[str, Any]:
    """Compute completeness, range, outlier jumps, and flatline diagnostics for one series."""
    total = len(values)
    valid_pairs = [
        (d, float(v))
        for d, v in zip(dates, values)
        if v is not None and math.isfinite(float(v))
    ]
    non_null = len(valid_pairs)
    null_count = total - non_null
    null_pct = round((null_count / total) * 100.0, 2) if total > 0 else 100.0

    if non_null == 0:
        return {
            "column": name,
            "total_rows": total,
            "non_null": 0,
            "null_pct": 100.0,
            "first_date": None,
            "last_date": None,
            "min": None,
            "median": None,
            "max": None,
            "out_of_range_count": 0,
            "extreme_jump_count": 0,
            "max_stuck_run": 0,
            "status": "CRITICAL",
            "issues": ["100% null — no data populated"],
        }

    nums = np.array([v for _, v in valid_pairs], dtype=np.float64)
    out_of_range = 0
    if min_ok is not None:
        out_of_range += int(np.sum(nums < min_ok))
    if max_ok is not None:
        out_of_range += int(np.sum(nums > max_ok))

    extreme_jumps = 0
    if max_daily_pct_jump is not None and len(valid_pairs) >= 2:
        for i in range(1, len(valid_pairs)):
            prev_v = valid_pairs[i - 1][1]
            cur_v = valid_pairs[i][1]
            if prev_v > 0 and abs((cur_v - prev_v) / prev_v) * 100.0 > max_daily_pct_jump:
                extreme_jumps += 1

    stuck_run = _max_consecutive_equal(values) if check_stuck else 0

    issues: list[str] = []
    status = "PASS"
    if null_pct >= 25.0:
        status = "CRITICAL"
        issues.append(f"High missing rate ({null_pct}% null)")
    elif null_pct >= 5.0:
        status = "WARNING"
        issues.append(f"Moderate missing rate ({null_pct}% null)")

    if out_of_range > 0:
        status = "CRITICAL"
        issues.append(f"{out_of_range} out-of-range values")
    if extreme_jumps > 0:
        if status == "PASS":
            status = "WARNING"
        issues.append(f"{extreme_jumps} single-step jumps > {max_daily_pct_jump}%")
    if check_stuck and stuck_run >= 10:
        if status == "PASS":
            status = "WARNING"
        issues.append(f"Flatlined/identical value for {stuck_run} consecutive rows")

    return {
        "column": name,
        "total_rows": total,
        "non_null": non_null,
        "null_pct": null_pct,
        "first_date": valid_pairs[0][0].isoformat(),
        "last_date": valid_pairs[-1][0].isoformat(),
        "min": round(float(np.min(nums)), 3),
        "median": round(float(np.median(nums)), 3),
        "max": round(float(np.max(nums)), 3),
        "out_of_range_count": out_of_range,
        "extreme_jump_count": extreme_jumps,
        "max_stuck_run": stuck_run,
        "status": status,
        "issues": issues,
    }


def audit_feature_matrix(
    daily_rows: list[dict[str, Any]],
    price_key: str = "brent_close_usd",
) -> list[dict[str, Any]]:
    """Audit all 47 candidate features for NaN rates, zero-variance, and train-vs-recent drift."""
    X, _, _ = build_gbt_feature_matrix(
        daily_rows,
        price_key=price_key,
        include_all_candidates=True,
    )
    n = X.shape[0]
    if n == 0:
        return []
    mid = n // 2
    ortho_set = set(ORTHOGONAL_FEATURE_NAMES)
    out: list[dict[str, Any]] = []

    for j, fname in enumerate(ALL_CANDIDATE_FEATURE_NAMES):
        col = X[:, j]
        valid = col[np.isfinite(col)]
        nan_pct = round(((n - len(valid)) / n) * 100.0, 2)
        label, group = FEATURE_METADATA.get(fname, (fname, "Other"))

        if len(valid) < 10:
            out.append({
                "feature": fname,
                "label": label,
                "group": group,
                "in_10f_model": fname in ortho_set,
                "nan_pct": nan_pct,
                "std": 0.0,
                "drift_z": None,
                "status": "CRITICAL",
                "issue": f"Insufficient valid values ({len(valid)}/{n})",
            })
            continue

        std_all = float(np.std(valid))
        mean_all = float(np.mean(valid))
        skew: Optional[float] = None
        kurt: Optional[float] = None
        outlier_pct: float = 0.0
        max_abs_z: Optional[float] = None
        if std_all > 1e-9:
            z_vals = (valid - mean_all) / std_all
            skew = round(float(np.mean(z_vals ** 3)), 2)
            kurt = round(float(np.mean(z_vals ** 4) - 3.0), 2)
            outlier_pct = round(float(np.mean(np.abs(z_vals) > 3.0)) * 100.0, 2)
            max_abs_z = round(float(np.max(np.abs(z_vals))), 2)

        needs_robust = (
            (skew is not None and abs(skew) >= 1.5)
            or (kurt is not None and kurt >= 5.0)
            or (max_abs_z is not None and max_abs_z >= 5.0)
        )
        recommended_scaler = "RobustScaler + Clip" if needs_robust else "StandardScaler OK"

        early = col[:mid]
        late = col[mid:]
        early_v = early[np.isfinite(early)]
        late_v = late[np.isfinite(late)]
        drift_z: Optional[float] = None
        if len(early_v) >= 20 and len(late_v) >= 20 and std_all > 1e-9:
            drift_z = round(abs(float(np.mean(late_v)) - float(np.mean(early_v))) / std_all, 2)

        status = "PASS"
        issue = None
        if nan_pct >= 25.0:
            status = "CRITICAL"
            issue = f"{nan_pct}% NaN in feature matrix"
        elif std_all <= 1e-9:
            status = "CRITICAL"
            issue = "Zero variance (constant feature)"
        elif drift_z is not None and drift_z >= 1.0:
            status = "WARNING"
            issue = f"Distribution shift between first half & second half (drift={drift_z}σ)"
        elif needs_robust:
            status = "WARNING"
            issue = f"Non-normal distribution (skew={skew}, kurt={kurt}, max|z|={max_abs_z}) — use RobustScaler + Clip"
        elif nan_pct >= 8.0:
            status = "WARNING"
            issue = f"{nan_pct}% NaN (mostly warmup or missing history)"

        out.append({
            "feature": fname,
            "label": label,
            "group": group,
            "in_10f_model": fname in ortho_set,
            "nan_pct": nan_pct,
            "std": round(std_all, 4),
            "skewness": skew,
            "excess_kurtosis": kurt,
            "outlier_pct_gt_3sigma": outlier_pct,
            "max_abs_z": max_abs_z,
            "recommended_scaler": recommended_scaler,
            "drift_z": drift_z,
            "status": status,
            "issue": issue,
        })

    return out


async def run_data_quality_audit(db: AsyncSession) -> dict[str, Any]:
    """Run full end-to-end data quality & integrity audit across all forecasting tables & ML features."""
    now = datetime.now(timezone.utc)
    today = now.date()
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
    fund_rows = list(
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
    memory = await build_news_analog_memory(db, symbol="BZ=F")

    # 1. Oil Prices Audit
    p_dates = [p.price_date for p in price_rows]
    price_gaps = [
        (p_dates[i - 1].isoformat(), p_dates[i].isoformat(), (p_dates[i] - p_dates[i - 1]).days)
        for i in range(1, len(p_dates))
        if (p_dates[i] - p_dates[i - 1]).days > 5
    ]
    brent_wti_diffs = [
        (p.brent_close_usd - p.wti_close_usd)
        if (p.brent_close_usd is not None and p.wti_close_usd is not None)
        else None
        for p in price_rows
    ]
    price_audits = [
        _audit_numeric_series("brent_close_usd", [p.brent_close_usd for p in price_rows], p_dates, min_ok=10.0, max_ok=250.0, max_daily_pct_jump=15.0),
        _audit_numeric_series("wti_close_usd", [p.wti_close_usd for p in price_rows], p_dates, min_ok=10.0, max_ok=250.0, max_daily_pct_jump=15.0),
        _audit_numeric_series("brent_minus_wti", brent_wti_diffs, p_dates, min_ok=-15.0, max_ok=25.0),
    ]

    # 2. Macro Daily Audit & Join Alignment
    m_dates = [m.date for m in macro_rows]
    macro_by_date = {m.date: m for m in macro_rows}
    price_date_set = set(p_dates)
    macro_date_set = set(m_dates)
    missing_macro_on_price_days = len(price_date_set - macro_date_set)

    macro_cols = [
        ("crack_spread_321_usd", -10.0, 100.0, None),
        ("brent_wti_spread_usd", -15.0, 25.0, None),
        ("dxy_close", 70.0, 140.0, 8.0),
        ("vix_close", 8.0, 95.0, 80.0),
        ("tanker_etf_close", 1.0, 200.0, 25.0),
        ("energy_etf_close", 10.0, 250.0, 15.0),
        ("copper_close", 1.5, 10.0, 15.0),
        ("china_etf_close", 10.0, 100.0, 20.0),
        ("natgas_close", 0.5, 20.0, 35.0),
        ("us10y_yield", 0.1, 10.0, 35.0),
        ("product_tanker_close", 5.0, 200.0, 25.0),
        ("curve_backwardation_proxy", 0.2, 5.0, 15.0),
    ]
    macro_audits = [
        _audit_numeric_series(col, [getattr(m, col) for m in macro_rows], m_dates, min_ok=lo, max_ok=hi, max_daily_pct_jump=jmp)
        for col, lo, hi, jmp in macro_cols
    ]

    # 3. Weekly Fundamentals Audit (CFTC + EIA)
    cftc_rows = [f for f in fund_rows if f.cftc_mm_net_pct_oi is not None]
    eia_rows = [f for f in fund_rows if f.eia_crude_stocks_kbbl is not None]
    fund_audits = [
        _audit_numeric_series(
            "cftc_mm_net_pct_oi",
            [f.cftc_mm_net_pct_oi for f in cftc_rows],
            [f.report_date for f in cftc_rows],
            min_ok=-60.0,
            max_ok=60.0,
            check_stuck=False,
        ),
        _audit_numeric_series(
            "eia_crude_stocks_kbbl",
            [f.eia_crude_stocks_kbbl for f in eia_rows],
            [f.report_date for f in eia_rows],
            min_ok=250_000.0,
            max_ok=650_000.0,
            check_stuck=False,
        ),
        _audit_numeric_series(
            "eia_crude_stocks_chg_kbbl",
            [f.eia_crude_stocks_chg_kbbl for f in eia_rows],
            [f.report_date for f in eia_rows],
            min_ok=-30_000.0,
            max_ok=30_000.0,
            check_stuck=False,
        ),
        _audit_numeric_series(
            "eia_refinery_util_pct",
            [f.eia_refinery_util_pct for f in eia_rows],
            [f.report_date for f in eia_rows],
            min_ok=40.0,
            max_ok=105.0,
            check_stuck=False,
        ),
    ]

    # 4. PortWatch Physical Tanker Flow Audit
    pw_by_date: dict[date, dict[str, float]] = {}
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

    pw_dates_sorted = sorted(pw_by_date.keys())
    missing_pw_on_price_days = len(price_date_set - set(pw_dates_sorted))
    pw_audits = [
        _audit_numeric_series(k, [pw_by_date[d][k] for d in pw_dates_sorted], pw_dates_sorted, min_ok=0.0, check_stuck=False)
        for k in ("pw_hormuz_tankers", "pw_bab_tankers", "pw_suez_tankers", "pw_cape_tankers", "pw_global_tankers")
    ]

    # 5. News Events Temporal Coverage Audit
    news_by_year = Counter(r.market_aligned_at.year for r in memory.records)
    news_nonzero_dir = sum(1 for r in memory.records if abs(r.effective_direction) > 0.05)

    # 6. Full 47-Feature Matrix Audit
    daily_rows = _assemble_gbt_daily_rows(
        price_rows=price_rows,
        macro_by_date=macro_by_date,
        pw_by_date=pw_by_date,
        fundamentals_rows=fund_rows,
        news_records=memory.records,
    )
    feature_audits = audit_feature_matrix(daily_rows, price_key="brent_close_usd")

    # Collect actionable findings
    findings: list[dict[str, str]] = []
    if p_dates and (today - p_dates[-1]).days > 4:
        findings.append({"severity": "WARNING", "area": "oil_prices", "message": f"Latest oil price is {(today - p_dates[-1]).days}d old ({p_dates[-1]})"})
    if price_gaps:
        findings.append({"severity": "WARNING", "area": "oil_prices", "message": f"{len(price_gaps)} calendar gaps > 5 days in oil_prices"})
    if missing_macro_on_price_days > 15:
        findings.append({"severity": "WARNING", "area": "join_alignment", "message": f"{missing_macro_on_price_days} oil_prices dates have no matching oil_macro_daily row"})
    if missing_pw_on_price_days > 15:
        findings.append({"severity": "WARNING", "area": "join_alignment", "message": f"{missing_pw_on_price_days} oil_prices dates have no matching PortWatch row"})
    if len(eia_rows) < 200:
        findings.append({"severity": "CRITICAL", "area": "eia_storage", "message": f"Only {len(eia_rows)} EIA weeks populated (expected ~260)"})
    if len(cftc_rows) < 200:
        findings.append({"severity": "CRITICAL", "area": "cftc_cot", "message": f"Only {len(cftc_rows)} CFTC weeks populated (expected ~260)"})

    for section_name, items in (
        ("oil_prices", price_audits),
        ("oil_macro_daily", macro_audits),
        ("oil_fundamentals_weekly", fund_audits),
        ("portwatch", pw_audits),
    ):
        for item in items:
            if item["status"] != "PASS":
                findings.append({
                    "severity": item["status"],
                    "area": f"{section_name}.{item['column']}",
                    "message": "; ".join(item["issues"]),
                })

    drifted_features = [f for f in feature_audits if f["status"] != "PASS"]
    for f in drifted_features:
        findings.append({
            "severity": f["status"],
            "area": f"feature.{f['feature']}",
            "message": f["issue"] or f["status"],
        })

    crit_n = sum(1 for x in findings if x["severity"] == "CRITICAL")
    warn_n = sum(1 for x in findings if x["severity"] == "WARNING")
    health_score = max(0, 100 - crit_n * 15 - warn_n * 3)

    return {
        "as_of": now.isoformat(),
        "health_score": health_score,
        "critical_count": crit_n,
        "warning_count": warn_n,
        "join_alignment": {
            "oil_price_days": len(price_rows),
            "macro_days": len(macro_rows),
            "missing_macro_on_price_days": missing_macro_on_price_days,
            "portwatch_days": len(pw_dates_sorted),
            "missing_portwatch_on_price_days": missing_pw_on_price_days,
            "price_gaps_gt_5d": price_gaps[:10],
            "cftc_weeks": len(cftc_rows),
            "eia_weeks": len(eia_rows),
        },
        "news_coverage": {
            "total_events": len(memory.records),
            "nonzero_direction_pct": round((news_nonzero_dir / max(1, len(memory.records))) * 100.0, 1),
            "events_by_year": dict(sorted(news_by_year.items())),
        },
        "findings": findings,
        "tables": {
            "oil_prices": price_audits,
            "oil_macro_daily": macro_audits,
            "oil_fundamentals_weekly": fund_audits,
            "portwatch": pw_audits,
        },
        "feature_matrix": feature_audits,
    }
