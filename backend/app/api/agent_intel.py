"""AI Agent Intelligence & Briefings API.

Provides a single unified, token-efficient context endpoint for external AI agents
(such as the scheduled GitHub Action) plus endpoints to store and retrieve the
agent's executive summaries and key points:

- ``GET /api/agent-intel/context``: Combines fleet analytics, Brent/WTI market intel,
  chokepoint/strait risk, top ranked news events, and the learned Category x Time-Interval
  price impact matrix into one compact JSON payload.
- ``POST /api/agent-intel/briefings``: Stores a generated AI briefing (optionally secured
  via ``AGENT_BRIEFING_TOKEN`` Bearer header).
- ``GET /api/agent-intel/briefings/latest``: Returns the most recent AI briefing for the UI.
- ``GET /api/agent-intel/briefings``: Lists historical AI briefings.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import and_, desc, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.news import _build_impact_matrix, _label_for_category
from app.config import settings
from app.database import get_db
from app.models import (
    AISGap,
    AIMarketBriefing,
    CargoEvent,
    NewsEvent,
    NewsEventPriceImpact,
    OilMarketSnapshot,
    OilPrice,
    STSEvent,
    StraitRiskDaily,
    Vessel,
)
from app.schemas.agent_intel import (
    AgentAnalyticsContext,
    AgentIntelContextResponse,
    AgentMarketIntelContext,
    AgentNewsEventDigest,
    AgentNewsIntelContext,
    AgentPriceSummary,
    BriefingCreateRequest,
    BriefingResponse,
)

router = APIRouter()


def _pct_change(latest: Optional[float], prev: Optional[float]) -> Optional[float]:
    if latest is None or prev is None or prev <= 0:
        return None
    return round(((latest - prev) / prev) * 100.0, 2)


def _to_pct(log_ret: Optional[float]) -> Optional[float]:
    return round(log_ret * 100.0, 2) if log_ret is not None else None


@router.get("/context", response_model=AgentIntelContextResponse)
async def get_agent_intel_context(
    days: int = Query(14, ge=1, le=90, description="Lookback window in days for events and market trends"),
    top_events: int = Query(15, ge=1, le=50, description="Max number of top-ranked news events to include"),
    db: AsyncSession = Depends(get_db),
):
    """Unified analytics + market intel + news intel JSON payload for AI analyst agents."""
    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=24)
    cutoff_window = now - timedelta(days=days)
    cutoff_date = (now - timedelta(days=max(days, 30))).date()

    # ── 1. Fleet & Physical Maritime Analytics ───────────────────────────────
    total_vessels = (await db.execute(select(func.count()).select_from(Vessel))).scalar() or 0
    in_transit = (
        await db.execute(select(func.count()).select_from(Vessel).where(Vessel.last_speed > 3))
    ).scalar() or 0
    at_port = (
        await db.execute(select(func.count()).select_from(Vessel).where(Vessel.last_speed <= 1))
    ).scalar() or 0
    currently_dark = (
        await db.execute(
            select(func.count())
            .select_from(AISGap)
            .where(and_(AISGap.status.in_(["dark", "extended_dark"]), AISGap.gap_end.is_(None)))
        )
    ).scalar() or 0
    ongoing_sts = (
        await db.execute(
            select(func.count())
            .select_from(STSEvent)
            .where(STSEvent.status.in_(["detected", "ongoing"]))
        )
    ).scalar() or 0
    cargo_events_24h = (
        await db.execute(
            select(func.count()).select_from(CargoEvent).where(CargoEvent.created_at >= cutoff_24h)
        )
    ).scalar() or 0
    daily_volume_barrels = float(
        (
            await db.execute(
                select(func.coalesce(func.sum(CargoEvent.estimated_volume_barrels), 0)).where(
                    CargoEvent.created_at >= cutoff_24h
                )
            )
        ).scalar()
        or 0.0
    )

    cp_rows = (
        await db.execute(
            text("""
                SELECT c.name, COUNT(*) AS cnt
                FROM chokepoint_transits ct
                JOIN chokepoints c ON ct.chokepoint_id = c.id
                WHERE ct.time >= :cutoff_24h
                GROUP BY c.name
                ORDER BY cnt DESC
            """),
            {"cutoff_24h": cutoff_24h},
        )
    ).all()
    chokepoint_transits_24h = {str(r.name): int(r.cnt) for r in cp_rows}

    analytics_ctx = AgentAnalyticsContext(
        total_vessels=int(total_vessels),
        in_transit=int(in_transit),
        at_port=int(at_port),
        currently_dark=int(currently_dark),
        ongoing_sts=int(ongoing_sts),
        cargo_events_24h=int(cargo_events_24h),
        daily_volume_barrels=round(daily_volume_barrels, 1),
        chokepoint_transits_24h=chokepoint_transits_24h,
    )

    # ── 2. Market Intel (Prices, Snapshots, Strait Risk) ─────────────────────
    price_rows = list(
        (
            await db.execute(
                select(OilPrice)
                .where(OilPrice.price_date >= cutoff_date)
                .order_by(OilPrice.price_date.desc())
            )
        ).scalars().all()
    )
    latest_p = price_rows[0] if price_rows else None
    prev_1d = price_rows[1] if len(price_rows) > 1 else None
    prev_7d = next(
        (p for p in price_rows if latest_p and (latest_p.price_date - p.price_date).days >= 7),
        price_rows[-1] if len(price_rows) > 1 else None,
    )

    price_summary = AgentPriceSummary(
        latest_date=latest_p.price_date if latest_p else None,
        brent_usd=latest_p.brent_close_usd if latest_p else None,
        wti_usd=latest_p.wti_close_usd if latest_p else None,
        brent_change_1d_pct=_pct_change(
            latest_p.brent_close_usd if latest_p else None,
            prev_1d.brent_close_usd if prev_1d else None,
        ),
        brent_change_7d_pct=_pct_change(
            latest_p.brent_close_usd if latest_p else None,
            prev_7d.brent_close_usd if prev_7d else None,
        ),
        wti_change_1d_pct=_pct_change(
            latest_p.wti_close_usd if latest_p else None,
            prev_1d.wti_close_usd if prev_1d else None,
        ),
        wti_change_7d_pct=_pct_change(
            latest_p.wti_close_usd if latest_p else None,
            prev_7d.wti_close_usd if prev_7d else None,
        ),
    )

    snaps = list(
        (
            await db.execute(
                select(OilMarketSnapshot)
                .where(OilMarketSnapshot.snapshot_date >= cutoff_date)
                .order_by(OilMarketSnapshot.snapshot_date.desc())
                .limit(14)
            )
        ).scalars().all()
    )
    latest_snap = snaps[0] if snaps else None
    snap_fields = (
        "total_active_vessels",
        "vessels_in_transit",
        "dark_vessels_count",
        "new_ais_gaps_24h",
        "sts_events_24h",
        "chokepoint_transits_24h",
        "strait_of_hormuz_transits",
        "bab_el_mandeb_transits",
        "suez_canal_transits",
        "cape_of_good_hope_transits",
        "floating_storage_count",
        "anchored_waiting_count",
        "avg_port_wait_hours",
        "news_event_count_24h",
        "news_net_impact_1d",
        "news_net_impact_7d",
        "news_supply_risk_score",
        "news_sanctions_pressure_7d",
        "news_opec_signal_7d",
        "news_hormuz_x_transit_drop",
        "news_sanctions_x_dark_fleet",
    )
    latest_snap_dict = (
        {f: getattr(latest_snap, f, None) for f in snap_fields} if latest_snap else {}
    )

    recent_7 = snaps[:7]
    avg_7d: dict[str, Optional[float]] = {}
    for f in (
        "strait_of_hormuz_transits",
        "cape_of_good_hope_transits",
        "bab_el_mandeb_transits",
        "dark_vessels_count",
        "floating_storage_count",
        "news_net_impact_1d",
        "news_supply_risk_score",
    ):
        vals = [getattr(s, f) for s in recent_7 if getattr(s, f, None) is not None]
        avg_7d[f] = round(sum(vals) / len(vals), 3) if vals else None

    latest_risk = (
        await db.execute(select(StraitRiskDaily).order_by(StraitRiskDaily.date.desc()).limit(1))
    ).scalar_one_or_none()
    strait_risk_dict = (
        {
            "date": latest_risk.date.isoformat(),
            "hormuz_crisis_pressure": latest_risk.hormuz_crisis_pressure,
            "hormuz_escalation_prob": latest_risk.hormuz_escalation_prob,
            "hormuz_stranded_vessels": latest_risk.hormuz_stranded_vessels,
            "hormuz_incidents_count": latest_risk.hormuz_incidents_count,
            "gdelt_strait_risk_vol": latest_risk.gdelt_strait_risk_vol,
            "gdelt_strait_tone": latest_risk.gdelt_strait_tone,
        }
        if latest_risk
        else {}
    )

    market_ctx = AgentMarketIntelContext(
        prices=price_summary,
        latest_snapshot_date=latest_snap.snapshot_date if latest_snap else None,
        latest_snapshot=latest_snap_dict,
        strait_risk_latest=strait_risk_dict,
        recent_7d_averages=avg_7d,
    )

    # ── 3. News Intel (24h Pressure, Top Ranked Events, Impact Matrix) ───────
    evs_24h = list(
        (
            await db.execute(
                select(NewsEvent).where(NewsEvent.first_seen_at >= cutoff_24h)
            )
        ).scalars().all()
    )
    high_impact_24h = sum(1 for e in evs_24h if (e.prior_rank_score or 0.0) >= 55.0)
    net_pressure = round(
        sum((e.effective_direction or 0.0) * ((e.prior_rank_score or 0.0) / 100.0) for e in evs_24h),
        2,
    )
    pressure_label = (
        "Bullish Supply Risk"
        if net_pressure >= 0.4
        else ("Bearish / Easing" if net_pressure <= -0.4 else "Balanced / Neutral")
    )

    ranked_events = list(
        (
            await db.execute(
                select(NewsEvent)
                .where(NewsEvent.first_seen_at >= cutoff_window)
                .order_by(NewsEvent.prior_rank_score.desc(), NewsEvent.first_seen_at.desc())
                .limit(top_events)
            )
        ).scalars().all()
    )

    brent_impacts: dict[int, NewsEventPriceImpact] = {}
    if ranked_events:
        imp_rows = (
            await db.execute(
                select(NewsEventPriceImpact).where(
                    NewsEventPriceImpact.event_id.in_([e.id for e in ranked_events]),
                    NewsEventPriceImpact.symbol == "BZ=F",
                )
            )
        ).scalars().all()
        brent_impacts = {imp.event_id: imp for imp in imp_rows}

    top_digests: list[AgentNewsEventDigest] = []
    for e in ranked_events:
        imp = brent_impacts.get(e.id)
        top_digests.append(
            AgentNewsEventDigest(
                id=e.id,
                headline=e.headline,
                event_type=e.event_type,
                event_type_label=_label_for_category(e.event_type),
                chokepoints=list(e.chokepoints or []),
                first_seen_at=e.first_seen_at,
                prior_rank_score=e.prior_rank_score,
                effective_direction=e.effective_direction,
                distinct_sources=e.distinct_sources,
                max_source_tier=e.max_source_tier,
                brent_ret_1h_pct=_to_pct(imp.ret_1h) if imp else None,
                brent_ret_4h_pct=_to_pct(imp.ret_4h) if imp else None,
                brent_ret_24h_pct=_to_pct(imp.ret_24h) if imp else None,
                brent_ret_3d_pct=_to_pct(imp.ret_3d) if imp else None,
                brent_ret_7d_pct=_to_pct(imp.ret_7d) if imp else None,
                llm_rationale=e.llm_rationale,
            )
        )

    matrix = await _build_impact_matrix(db, "BZ=F")
    compact_matrix = [
        {
            "category": row.event_type_label,
            "total_events": row.total_events,
            "peak_horizon": row.peak_horizon,
            "peak_mean_ret_pct": row.peak_mean_ret_pct,
            "peak_hit_rate_pct": row.peak_hit_rate_pct,
            "persistence": row.persistence_label,
            "half_life_hours": row.half_life_hours,
        }
        for row in matrix.rows
        if row.total_events > 0 or row.peak_horizon is not None
    ]

    news_ctx = AgentNewsIntelContext(
        events_24h=len(evs_24h),
        high_impact_events_24h=high_impact_24h,
        net_pressure_24h=net_pressure,
        pressure_label=pressure_label,
        top_events=top_digests,
        impact_matrix_brent=compact_matrix,
    )

    # ── 4. Verified ML Risk Signals & 4-Pillar Forecast for AI Analyst ───────
    forecast_ml_ctx: dict = {}
    try:
        from app.services.oil_price_predictor import build_bottom_line_forecast

        bl = await build_bottom_line_forecast(db, symbol="BZ=F")
        forecast_ml_ctx = {
            "symbol": bl.symbol,
            "current_price_usd": bl.current_price_usd,
            "composite_score": bl.composite_score,
            "directional_verdict": bl.verdict,
            "headline_summary": bl.headline_summary,
            "pillars": [
                {"label": p.label, "score": p.score, "direction": p.direction, "summary": p.summary}
                for p in bl.pillars
            ],
            "horizons": {
                hk: {
                    "predicted_return_pct": hv.predicted_return_pct,
                    "price_range_usd": [hv.price_low_usd, hv.price_high_usd],
                    "har_ovx_volatility": hv.volatility_alert.model_dump() if hv.volatility_alert else None,
                    "supply_shock_spike": hv.spike_radar.model_dump() if hv.spike_radar else None,
                }
                for hk, hv in bl.horizons.items()
            },
        }
    except Exception:
        forecast_ml_ctx = {}

    return AgentIntelContextResponse(
        generated_at=now,
        window_days=days,
        analytics=analytics_ctx,
        market_intel=market_ctx,
        news_intel=news_ctx,
        forecast_ml=forecast_ml_ctx,
    )


@router.post("/briefings", response_model=BriefingResponse)
async def create_briefing(
    payload: BriefingCreateRequest,
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """Store an AI-generated market briefing (called by the GitHub Action analyst)."""
    required_token = (settings.AGENT_BRIEFING_TOKEN or "").strip()
    if required_token:
        provided = (authorization or "").replace("Bearer ", "").strip()
        if provided != required_token:
            raise HTTPException(status_code=401, detail="Invalid or missing briefing token")

    briefing = AIMarketBriefing(
        window_days=payload.window_days,
        model_used=(payload.model_used or "")[:128] or None,
        market_bias=payload.market_bias,
        confidence=payload.confidence,
        executive_summary=payload.executive_summary.strip(),
        key_points=[p.strip() for p in payload.key_points if p and p.strip()][:12],
        watch_items=[w.strip() for w in payload.watch_items if w and w.strip()][:8],
        full_markdown=payload.full_markdown,
        context_snapshot=payload.context_snapshot,
    )
    db.add(briefing)
    await db.commit()
    await db.refresh(briefing)
    return BriefingResponse.model_validate(briefing)


@router.get("/briefings/latest", response_model=Optional[BriefingResponse])
async def get_latest_briefing(db: AsyncSession = Depends(get_db)):
    """Return the most recent stored AI market briefing (or null if none yet)."""
    row = (
        await db.execute(
            select(AIMarketBriefing).order_by(desc(AIMarketBriefing.created_at)).limit(1)
        )
    ).scalar_one_or_none()
    return BriefingResponse.model_validate(row) if row else None


@router.get("/briefings", response_model=list[BriefingResponse])
async def list_briefings(
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """List recent AI market briefings ordered newest first."""
    rows = (
        await db.execute(
            select(AIMarketBriefing).order_by(desc(AIMarketBriefing.created_at)).limit(limit)
        )
    ).scalars().all()
    return [BriefingResponse.model_validate(r) for r in rows]
