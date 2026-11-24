"""News & Event Intelligence REST API endpoints.

Provides:
- ``GET /api/news/events``: Ranked canonical oil & maritime events with Brent/WTI multi-horizon reactions.
- ``GET /api/news/impact-matrix``: Category x Time-Interval learned price-impact matrix (peak window, hit rate, t-stat).
- ``GET /api/news/summary``: High-level KPI strip for the News Intelligence dashboard.
- ``POST /api/news/refresh``: Trigger an on-demand fetch + optional LLM enrichment + impact analysis run.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.news import (
    NewsArticle,
    NewsEvent,
    NewsEventPriceImpact,
    NewsImpactPrior,
)
from app.schemas.news import (
    EventPricePrediction,
    EventPriceReaction,
    HorizonStatCell,
    ImpactMatrixResponse,
    ImpactMatrixRow,
    NewsArticleItem,
    NewsEventResponse,
    NewsSummaryResponse,
)
from app.services.news_clusterer import EVENT_CATEGORIES
from app.services.news_fetcher import NewsFetcher
from app.services.news_impact_analyzer import NewsImpactAnalyzer, POST_HORIZONS
from app.services.news_llm_enricher import NewsLLMEnricher, is_llm_configured
from app.services.news_predictor import build_news_analog_memory

router = APIRouter()

MATRIX_HORIZON_ORDER = ["pre_24h", "pre_4h", "1h", "4h", "24h", "3d", "7d", "14d"]


def _label_for_category(code: str) -> str:
    spec = EVENT_CATEGORIES.get(code)
    return spec.label if spec else code.replace("_", " ").title()


def _persistence_label(horizons: dict[str, HorizonStatCell]) -> str:
    c4 = horizons.get("4h")
    c24 = horizons.get("24h")
    c7d = horizons.get("7d")
    base = c4 if (c4 and c4.mean_z is not None and c4.sample_count >= 3) else c24
    if not base or base.mean_z is None or base.sample_count < 3:
        return "Learning"
    if c7d and c7d.mean_z is not None and c7d.sample_count >= 3:
        if abs(base.mean_z) < 0.05:
            return "Slow drift" if abs(c7d.mean_z) >= 0.2 else "Neutral"
        ratio = c7d.mean_z / base.mean_z
        if ratio >= 1.15:
            return "Compounds"
        if ratio >= 0.45:
            return "Persists"
        return "Mean-reverts"
    return "Short-term"


async def _hydrate_events(
    db: AsyncSession, events: list[NewsEvent], include_articles: bool = True
) -> list[NewsEventResponse]:
    if not events:
        return []
    event_ids = [e.id for e in events]

    impacts_rows = (
        await db.execute(
            select(NewsEventPriceImpact).where(NewsEventPriceImpact.event_id.in_(event_ids))
        )
    ).scalars().all()
    impacts_by_ev: dict[int, dict[str, EventPriceReaction]] = defaultdict(dict)
    for imp in impacts_rows:
        impacts_by_ev[imp.event_id][imp.symbol] = EventPriceReaction.model_validate(imp)

    articles_by_ev: dict[int, list[NewsArticleItem]] = defaultdict(list)
    if include_articles:
        art_rows = (
            await db.execute(
                select(NewsArticle)
                .where(NewsArticle.event_id.in_(event_ids))
                .order_by(NewsArticle.source_tier.asc(), NewsArticle.published_at.asc())
            )
        ).scalars().all()
        for a in art_rows:
            if a.event_id is not None and len(articles_by_ev[a.event_id]) < 6:
                articles_by_ev[a.event_id].append(NewsArticleItem.model_validate(a))

    out: list[NewsEventResponse] = []
    for e in events:
        imps = impacts_by_ev.get(e.id, {})
        out.append(
            NewsEventResponse(
                id=e.id,
                headline=e.headline,
                summary=e.summary,
                event_type=e.event_type,
                event_type_label=_label_for_category(e.event_type),
                chokepoints=list(e.chokepoints or []),
                entities=list(e.entities or []),
                primary_region=e.primary_region or "global",
                first_seen_at=e.first_seen_at,
                market_aligned_at=e.market_aligned_at,
                article_count=e.article_count,
                distinct_sources=e.distinct_sources,
                max_source_tier=e.max_source_tier,
                novelty_score=e.novelty_score,
                effective_direction=e.effective_direction,
                effective_severity=e.effective_severity,
                prior_rank_score=e.prior_rank_score,
                learned_impact_score=e.learned_impact_score,
                llm_enriched=e.llm_enriched_at is not None,
                llm_confidence=e.llm_confidence,
                llm_estimated_mbpd=e.llm_estimated_mbpd,
                llm_rationale=e.llm_rationale,
                brent_impact=imps.get("BZ=F"),
                wti_impact=imps.get("CL=F"),
                articles=articles_by_ev.get(e.id, []),
            )
        )
    return out


async def _build_impact_matrix(db: AsyncSession, symbol: str) -> ImpactMatrixResponse:
    latest_date = (
        await db.execute(
            select(func.max(NewsImpactPrior.as_of_date)).where(NewsImpactPrior.symbol == symbol)
        )
    ).scalar()

    priors: list[NewsImpactPrior] = []
    if latest_date is not None:
        priors = list(
            (
                await db.execute(
                    select(NewsImpactPrior).where(
                        NewsImpactPrior.as_of_date == latest_date,
                        NewsImpactPrior.symbol == symbol,
                        NewsImpactPrior.region_scope == "global",
                    )
                )
            ).scalars().all()
        )

    # Also get total event counts per category so even fresh categories appear cleanly
    cat_counts_rows = (
        await db.execute(
            select(NewsEvent.event_type, func.count(NewsEvent.id)).group_by(NewsEvent.event_type)
        )
    ).all()
    cat_counts = {row[0]: int(row[1]) for row in cat_counts_rows}

    by_cat: dict[str, dict[str, NewsImpactPrior]] = defaultdict(dict)
    for pr in priors:
        by_cat[pr.event_type][pr.horizon] = pr

    all_cats = list(EVENT_CATEGORIES.keys())
    for c in by_cat.keys():
        if c not in all_cats:
            all_cats.append(c)

    rows: list[ImpactMatrixRow] = []
    for cat in all_cats:
        p_map = by_cat.get(cat, {})
        horizons: dict[str, HorizonStatCell] = {}
        peak_h: Optional[str] = None
        peak_score = -1.0
        peak_ret_pct: Optional[float] = None
        peak_hit_pct: Optional[float] = None
        peak_t: Optional[float] = None
        hl_hours: Optional[float] = None

        for h in MATRIX_HORIZON_ORDER:
            pr = p_map.get(h)
            if pr:
                ret_pct = round(pr.mean_ret * 100.0, 2) if pr.mean_ret is not None else None
                med_pct = round(pr.median_ret * 100.0, 2) if pr.median_ret is not None else None
                hit_pct = round(pr.hit_rate * 100.0, 1) if pr.hit_rate is not None else None
                cell = HorizonStatCell(
                    horizon=h,
                    sample_count=pr.sample_count,
                    mean_ret_pct=ret_pct,
                    median_ret_pct=med_pct,
                    mean_z=round(pr.mean_z, 2) if pr.mean_z is not None else None,
                    hit_rate_pct=hit_pct,
                    t_stat=round(pr.t_stat, 2) if pr.t_stat is not None else None,
                    shrunk_weight=round(pr.shrunk_weight, 3) if pr.shrunk_weight is not None else 0.0,
                )
                horizons[h] = cell
                if pr.half_life_hours is not None:
                    hl_hours = round(pr.half_life_hours, 1)
                if h in POST_HORIZONS and pr.sample_count > 0:
                    # Pick peak horizon by |shrunk_weight| (balances magnitude + sample count)
                    score = abs(pr.shrunk_weight or 0.0)
                    if score > peak_score:
                        peak_score = score
                        peak_h = h
                        peak_ret_pct = ret_pct
                        peak_hit_pct = hit_pct
                        peak_t = round(pr.t_stat, 2) if pr.t_stat is not None else None
            else:
                horizons[h] = HorizonStatCell(horizon=h)

        rows.append(
            ImpactMatrixRow(
                event_type=cat,
                event_type_label=_label_for_category(cat),
                total_events=cat_counts.get(cat, 0),
                peak_horizon=peak_h,
                peak_mean_ret_pct=peak_ret_pct,
                peak_hit_rate_pct=peak_hit_pct,
                peak_t_stat=peak_t,
                persistence_label=_persistence_label(horizons),
                half_life_hours=hl_hours,
                horizons=horizons,
            )
        )

    # Sort by strongest learned signal first, then by total events
    rows.sort(
        key=lambda r: (
            abs(r.horizons[r.peak_horizon].shrunk_weight) if r.peak_horizon and r.peak_horizon in r.horizons else 0.0,
            r.total_events,
        ),
        reverse=True,
    )

    return ImpactMatrixResponse(
        symbol=symbol,
        as_of_date=latest_date,
        horizon_order=MATRIX_HORIZON_ORDER,
        rows=rows,
    )


@router.get("/events", response_model=list[NewsEventResponse])
async def list_news_events(
    days: int = Query(14, ge=1, le=365),
    event_type: Optional[str] = Query(None),
    chokepoint: Optional[str] = Query(None),
    min_rank: float = Query(0.0, ge=0.0, le=100.0),
    sort_by: str = Query("rank", pattern="^(rank|recent|learned)$"),
    symbol: str = Query("BZ=F", pattern="^(BZ=F|CL=F)$"),
    include_predictions: bool = Query(True),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    """List ranked canonical oil & maritime news events with multi-horizon Brent/WTI impacts & analog predictions."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    q = select(NewsEvent).where(
        NewsEvent.first_seen_at >= cutoff,
        NewsEvent.prior_rank_score >= min_rank,
    )
    if event_type:
        q = q.where(NewsEvent.event_type == event_type)
    if chokepoint:
        q = q.where(NewsEvent.chokepoints.contains([chokepoint]))

    if sort_by == "recent":
        q = q.order_by(NewsEvent.first_seen_at.desc())
    elif sort_by == "learned":
        q = q.order_by(NewsEvent.learned_impact_score.desc().nullslast(), NewsEvent.prior_rank_score.desc())
    else:
        q = q.order_by(NewsEvent.prior_rank_score.desc(), NewsEvent.first_seen_at.desc())

    events = list((await db.execute(q.limit(limit))).scalars().all())
    hydrated = await _hydrate_events(db, events, include_articles=True)

    if include_predictions and hydrated:
        memory = await build_news_analog_memory(db, symbol=symbol)
        now_utc = datetime.now(timezone.utc)
        for item in hydrated:
            rec = memory.by_id.get(item.id)
            if rec is not None:
                item.prediction = memory.predict_for_event(rec, symbol=symbol, reference_time=now_utc)

    return hydrated


@router.get("/events/{event_id}/prediction", response_model=EventPricePrediction)
async def get_event_prediction(
    event_id: int,
    symbol: str = Query("BZ=F", pattern="^(BZ=F|CL=F)$"),
    db: AsyncSession = Depends(get_db),
):
    """Return similarity-based historical analog price prediction for a single news event."""
    memory = await build_news_analog_memory(db, symbol=symbol)
    rec = memory.by_id.get(event_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="News event not found")
    return memory.predict_for_event(rec, symbol=symbol, reference_time=datetime.now(timezone.utc))


@router.get("/impact-matrix", response_model=ImpactMatrixResponse)
async def get_impact_matrix(
    symbol: str = Query("BZ=F", pattern="^(BZ=F|CL=F)$"),
    db: AsyncSession = Depends(get_db),
):
    """Return the learned Category x Time-Interval price impact matrix for Brent (BZ=F) or WTI (CL=F)."""
    return await _build_impact_matrix(db, symbol)


@router.get("/summary", response_model=NewsSummaryResponse)
async def get_news_summary(db: AsyncSession = Depends(get_db)):
    """High-level KPI summary for the News Intelligence dashboard."""
    now = datetime.now(timezone.utc)
    cutoff_24h = now - timedelta(hours=24)
    cutoff_30d = now - timedelta(days=30)

    evs_24h = list(
        (
            await db.execute(
                select(NewsEvent)
                .where(NewsEvent.first_seen_at >= cutoff_24h)
                .order_by(NewsEvent.prior_rank_score.desc())
            )
        ).scalars().all()
    )
    total_30d = (
        await db.execute(
            select(func.count()).select_from(NewsEvent).where(NewsEvent.first_seen_at >= cutoff_30d)
        )
    ).scalar() or 0

    high_impact_24h = sum(1 for e in evs_24h if e.prior_rank_score >= 55.0)
    net_pressure = sum((e.effective_direction or 0.0) * (e.prior_rank_score / 100.0) for e in evs_24h)
    net_pressure = round(net_pressure, 2)
    if net_pressure >= 0.4:
        pressure_label = "Bullish Supply Risk"
    elif net_pressure <= -0.4:
        pressure_label = "Bearish / Easing"
    else:
        pressure_label = "Balanced / Neutral"

    top_ev_resp = (await _hydrate_events(db, evs_24h[:1], include_articles=False))[0] if evs_24h else None

    matrix = await _build_impact_matrix(db, "BZ=F")
    strongest = next((r for r in matrix.rows if r.peak_horizon is not None), None)

    llm_active = is_llm_configured()
    return NewsSummaryResponse(
        events_24h=len(evs_24h),
        high_impact_events_24h=high_impact_24h,
        total_events_30d=int(total_30d),
        net_pressure_24h=net_pressure,
        pressure_label=pressure_label,
        top_event_24h=top_ev_resp,
        strongest_learned_driver=strongest,
        llm_enabled=llm_active,
        llm_provider=settings.NEWS_LLM_PROVIDER if llm_active else None,
        scoring_mode="Hybrid (Rules + Optional LLM + Empirical Lag)" if llm_active else "Local Rules + Empirical Lag (LLM Off)",
    )


@router.post("/refresh")
async def trigger_news_refresh(
    timespan: str = Query("7d", pattern="^(1d|3d|7d|30d)$"),
):
    """On-demand refresh: fetch free news, optionally enrich top clusters, and recompute price impacts."""
    fetcher = NewsFetcher()
    fetch_stats = await fetcher.fetch_and_store(timespan=timespan, include_rss=True)

    llm_enriched = 0
    if is_llm_configured():
        enricher = NewsLLMEnricher()
        llm_enriched = await enricher.enrich_pending_events(limit=10)

    analyzer = NewsImpactAnalyzer()
    impact_stats = await analyzer.run_once()

    return {
        "fetch": fetch_stats,
        "llm_enriched": llm_enriched,
        "impact": impact_stats,
        "llm_enabled": is_llm_configured(),
    }


@router.post("/recompute")
async def recompute_news_pipeline(
    days: Optional[int] = Query(
        None,
        ge=1,
        le=730,
        description="Optional lookback days to replay. Omit to recompute the entire Bronze archive.",
    ),
    recalculate_snapshots: bool = Query(
        True,
        description="Also recompute Gold daily ML features in oil_market_snapshots.",
    ),
    db: AsyncSession = Depends(get_db),
):
    """Full Bronze -> Silver -> Gold deterministic replay when classification, clustering, or ranking logic changes.

    1. Bronze -> Silver: Rebuilds `news_events` from raw `news_articles` (preserving existing LLM fields by cluster_key).
    2. Silver -> Gold 1: Recomputes multi-horizon `news_event_price_impacts` and walk-forward `news_impact_priors`.
    3. Gold 1 -> Gold 2: Optionally recalculates `oil_market_snapshots` with the updated news & AIS x News features.
    """
    from app.models.global_oil_features import OilMarketSnapshot
    from app.services.global_oil_feature_builder import GlobalOilFeatureBuilder

    fetcher = NewsFetcher()
    silver_stats = await fetcher.rebuild_events_from_raw(days=days)

    analyzer = NewsImpactAnalyzer()
    impacts_updated = await analyzer.compute_event_impacts(lookback_days=days or 730)
    priors_stats = await analyzer.backfill_walkforward_priors(days=days or 180)

    snapshots_recalculated = 0
    if recalculate_snapshots:
        now_utc = datetime.now(timezone.utc)
        today = now_utc.date()
        yesterday = today - timedelta(days=1)
        snap_q = select(OilMarketSnapshot.snapshot_date).order_by(OilMarketSnapshot.snapshot_date.asc())
        if days is not None:
            snap_q = snap_q.where(OilMarketSnapshot.snapshot_date >= today - timedelta(days=days))
        existing_dates = (await db.execute(snap_q)).scalars().all()
        dates_to_recalc = sorted(set(existing_dates) | {yesterday, today})
        builder = GlobalOilFeatureBuilder()
        for target_date in dates_to_recalc:
            await builder.aggregate(target_date=target_date)
        snapshots_recalculated = len(dates_to_recalc)

    return {
        "bronze_to_silver": silver_stats,
        "gold_impacts_updated": impacts_updated,
        "gold_priors": priors_stats,
        "gold_snapshots_recalculated": snapshots_recalculated,
    }


@router.post("/backfill")
async def backfill_historical_news(
    days: int = Query(365, ge=7, le=1825, description="How many days back to backfill via date-sliced Google News RSS (up to 5 years)."),
    chunk_days: int = Query(14, ge=7, le=60, description="Date-window size in days per RSS slice."),
    recalculate_snapshots: bool = Query(True, description="Also update oil_market_snapshots news columns."),
    db: AsyncSession = Depends(get_db),
):
    """Backfill multi-year Brent/WTI prices + historical oil & maritime news, compute multi-horizon impacts, and update walk-forward priors."""
    from app.models.global_oil_features import OilMarketSnapshot
    from app.services.global_oil_feature_builder import GlobalOilFeatureBuilder
    from app.services.oil_price_fetcher import OilPriceFetcher

    # Ensure daily Brent/WTI prices cover the full backfill window before computing event impacts
    price_fetcher = OilPriceFetcher()
    await price_fetcher.fetch_prices(days=days + 30)

    fetcher = NewsFetcher()
    backfill_stats = await fetcher.backfill_history(days=days, chunk_days=chunk_days)

    analyzer = NewsImpactAnalyzer()
    impacts_updated = await analyzer.compute_event_impacts(lookback_days=days + 14)
    priors_stats = await analyzer.backfill_walkforward_priors(days=min(days, 365))

    snapshots_recalculated = 0
    if recalculate_snapshots:
        today = datetime.now(timezone.utc).date()
        snap_q = (
            select(OilMarketSnapshot.snapshot_date)
            .where(OilMarketSnapshot.snapshot_date >= today - timedelta(days=min(days, 730)))
            .order_by(OilMarketSnapshot.snapshot_date.asc())
        )
        existing_dates = (await db.execute(snap_q)).scalars().all()
        builder = GlobalOilFeatureBuilder()
        for target_date in sorted(set(existing_dates) | {today - timedelta(days=1), today}):
            await builder.aggregate(target_date=target_date)
        snapshots_recalculated = len(set(existing_dates) | {today - timedelta(days=1), today})

    return {
        "backfill": backfill_stats,
        "gold_impacts_updated": impacts_updated,
        "gold_priors": priors_stats,
        "gold_snapshots_recalculated": snapshots_recalculated,
    }

