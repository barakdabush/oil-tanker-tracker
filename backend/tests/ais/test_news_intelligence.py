"""Unit tests for the News & Event Intelligence pipeline.

Covers:
- Domain taxonomy classification, chokepoint extraction, and relevance gating
- Market-hours weekend alignment & prior ranking formula
- URL normalization and clustering similarity
- RSS 2.0 / Atom XML parsing
- Strictly optional LLM enricher (safe no-op when disabled or missing API key)
- Intraday rolling volatility & Empirical Bayes multi-horizon lag learning
"""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.services.news_clusterer import (
    align_to_market_time,
    classify_article,
    compute_cluster_similarity,
    compute_prior_rank,
    normalize_url,
    tokenize_for_clustering,
    url_hash,
)
from app.services.news_fetcher import parse_rss_xml
from app.services.news_impact_analyzer import (
    compute_empirical_bayes_stats,
    compute_z_score,
    estimate_half_life_hours,
)
from app.services.news_llm_enricher import (
    NewsLLMEnricher,
    is_llm_configured,
    parse_llm_json,
)
from app.services.oil_price_fetcher import compute_rolling_daily_vol


def test_classify_chokepoint_attack_bullish():
    res = classify_article(
        "Houthi missile strike hits crude oil tanker in Bab el-Mandeb near Red Sea",
        "UKMTO confirmed an explosion aboard a VLCC transiting the Red Sea.",
        gdelt_tone=-6.5,
    )
    assert res.relevant is True
    assert res.event_type == "chokepoint_attack_blockade"
    assert res.direction > 0.5
    assert res.severity >= 0.85
    assert "Bab el-Mandeb" in res.chokepoints


def test_classify_opec_hike_bearish():
    res = classify_article(
        "OPEC+ agrees to unwind cuts and boost output by 500,000 bpd",
        "Saudi Arabia and allies plan to raise output next month.",
    )
    assert res.relevant is True
    assert res.event_type == "opec_production_decision"
    assert res.direction < -0.4


def test_classify_filters_irrelevant_articles():
    res = classify_article(
        "Local basketball team wins regional championship",
        "Fans celebrated downtown after a thrilling overtime victory.",
    )
    assert res.relevant is False


def test_normalize_url_strips_tracking_params():
    u1 = "https://gcaptain.com/tanker-news?utm_source=rss&id=42#top"
    u2 = "https://gcaptain.com/tanker-news?id=42"
    assert normalize_url(u1) == normalize_url(u2)
    assert url_hash(u1) == url_hash(u2)


def test_align_to_market_time_snaps_weekend_to_sunday_open():
    # Saturday 14:30 UTC -> Sunday 22:00 UTC
    sat = datetime(2026, 11, 21, 14, 30, tzinfo=timezone.utc)
    aligned = align_to_market_time(sat)
    assert aligned == datetime(2026, 11, 22, 22, 0, tzinfo=timezone.utc)

    # Wednesday 10:15 UTC -> unchanged
    wed = datetime(2026, 11, 18, 10, 15, tzinfo=timezone.utc)
    assert align_to_market_time(wed) == wed


def test_cluster_similarity_matches_same_incident():
    t1 = tokenize_for_clustering("Tanker struck by missile in Strait of Hormuz near Fujairah")
    t2 = tokenize_for_clustering("Crude oil tanker hit by missile attack in Strait of Hormuz")
    sim = compute_cluster_similarity(
        t1, t2,
        ["Strait of Hormuz"], ["Strait of Hormuz"],
        "chokepoint_attack_blockade", "chokepoint_attack_blockade",
    )
    assert sim >= 0.45


def test_prior_rank_rewards_tier1_corroboration_and_hormuz():
    high = compute_prior_rank(
        max_source_tier=1,
        distinct_sources=6,
        chokepoints=["Strait of Hormuz"],
        severity=0.95,
        novelty_score=1.0,
    )
    low = compute_prior_rank(
        max_source_tier=3,
        distinct_sources=1,
        chokepoints=[],
        severity=0.35,
        novelty_score=0.3,
    )
    assert high > 80.0
    assert low < 40.0
    assert high > low


def test_parse_rss_xml_handles_rss2_and_atom():
    rss = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel>
      <item>
        <title>Tanker traffic slows in Strait of Hormuz</title>
        <link>https://example.com/hormuz-1</link>
        <description>&lt;p&gt;Insurance rates rise.&lt;/p&gt;</description>
        <pubDate>Thu, 19 Nov 2026 12:00:00 GMT</pubDate>
      </item>
    </channel></rss>"""
    items = parse_rss_xml(rss, "gCaptain", 2)
    assert len(items) == 1
    assert items[0].source == "gCaptain"
    assert items[0].source_tier == 2
    assert items[0].summary == "Insurance rates rise."
    assert items[0].published_at.year == 2026


@pytest.mark.asyncio
async def test_llm_enricher_noops_safely_when_unconfigured():
    with (
        patch("app.services.news_llm_enricher.settings.NEWS_LLM_ENABLED", False),
        patch("app.services.news_llm_enricher.settings.NEWS_LLM_API_KEY", ""),
    ):
        assert is_llm_configured() is False
        enricher = NewsLLMEnricher()
        assert await enricher.enrich_pending_events() == 0

    # Even if enabled=True, missing API key must still safely no-op
    with (
        patch("app.services.news_llm_enricher.settings.NEWS_LLM_ENABLED", True),
        patch("app.services.news_llm_enricher.settings.NEWS_LLM_API_KEY", "   "),
    ):
        assert is_llm_configured() is False
        enricher = NewsLLMEnricher()
        assert await enricher.enrich_pending_events() == 0


def test_parse_llm_json_handles_fenced_and_invalid():
    fenced = """```json
    {"event_type": "sanctions_enforcement", "direction": 0.7, "severity": 0.8, "confidence": 0.9, "estimated_mbpd_at_risk": 1.2, "rationale": "New OFAC tanker sanctions tighten Russian crude flows over 3-7d."}
    ```"""
    parsed = parse_llm_json(fenced)
    assert parsed is not None
    assert parsed["event_type"] == "sanctions_enforcement"
    assert parsed["direction"] == 0.7
    assert parsed["estimated_mbpd"] == 1.2
    assert parse_llm_json("not valid json") is None


def test_empirical_bayes_and_z_score_math():
    z = compute_z_score(log_ret=0.04, daily_vol=0.02, horizon_hours=24)
    assert z == 2.0

    # With n=5 and k=15, shrunk_weight should be 5/20 = 0.25 * mean_z
    rets = [0.02, 0.03, 0.01, 0.025, -0.005]
    zs = [1.0, 1.5, 0.5, 1.25, -0.25]
    dirs = [0.8, 0.8, 0.7, 0.9, 0.6]
    st = compute_empirical_bayes_stats(rets, zs, dirs, shrinkage_k=15.0)
    assert st["sample_count"] == 5
    assert st["hit_rate"] == 0.8
    assert st["mean_z"] == 0.8
    assert st["shrunk_weight"] == 0.2
    assert st["t_stat"] > 2.0


def test_estimate_half_life_reversion_vs_persistence():
    # Peaks at 4h (1.2z) and drops below 0.6z by 24h -> fast reversion (~20h)
    hl_revert = estimate_half_life_hours({"1h": 0.5, "4h": 1.2, "24h": 0.4, "3d": 0.1}, default_hours=48.0)
    assert hl_revert == 20.0

    # Persists through 7d -> long half-life
    hl_persist = estimate_half_life_hours({"1h": 0.2, "4h": 0.5, "24h": 0.9, "3d": 1.1, "7d": 1.3}, default_hours=48.0)
    assert hl_persist >= 168.0


def test_compute_rolling_daily_vol():
    closes = [70.0 + (i % 2) * 0.7 for i in range(40)]
    vols = compute_rolling_daily_vol(closes)
    assert len(vols) == 40
    assert vols[0] is None
    assert vols[-1] is not None and vols[-1] > 0


@pytest.mark.asyncio
async def test_ingest_items_persists_irrelevant_articles_in_bronze():
    """Verify raw articles are ALWAYS persisted in news_articles even when filtered out (event_id=None)."""
    from unittest.mock import AsyncMock, MagicMock
    from app.models.news import NewsArticle, NewsEvent
    from app.services.news_fetcher import NewsFetcher, RawNewsItem

    added_objects = []
    next_id = 1

    mock_session = MagicMock()

    def add_side_effect(obj):
        added_objects.append(obj)

    async def flush_side_effect():
        nonlocal next_id
        for obj in added_objects:
            if isinstance(obj, NewsEvent) and obj.id is None:
                obj.id = next_id
                next_id += 1

    def exec_side_effect(*args, **kwargs):
        res = MagicMock()
        res.scalars.return_value.all.return_value = []
        res.all.return_value = []
        return res

    mock_session.add = MagicMock(side_effect=add_side_effect)
    mock_session.flush = AsyncMock(side_effect=flush_side_effect)
    mock_session.execute = AsyncMock(side_effect=exec_side_effect)
    mock_session.commit = AsyncMock()

    class MockAsyncSession:
        async def __aenter__(self):
            return mock_session
        async def __aexit__(self, exc_type, exc, tb):
            pass

    items = [
        RawNewsItem(
            url="https://example.com/basketball",
            source="SportsFeed",
            source_tier=3,
            title="Local basketball team wins championship",
            summary="Great game last night.",
            published_at=datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc),
        ),
        RawNewsItem(
            url="https://example.com/hormuz-strike",
            source="gCaptain",
            source_tier=2,
            title="Oil tanker struck by missile in Strait of Hormuz",
            summary="Maritime incident reported near Fujairah.",
            published_at=datetime(2026, 11, 19, 11, 0, tzinfo=timezone.utc),
        ),
    ]

    with patch("app.services.news_fetcher.async_session", return_value=MockAsyncSession()):
        fetcher = NewsFetcher()
        stats = await fetcher.ingest_items(items)

    assert stats["new_articles"] == 2
    assert stats["new_events"] == 1
    articles = [o for o in added_objects if isinstance(o, NewsArticle)]
    assert len(articles) == 2
    sports_art = next(a for a in articles if "basketball" in a.title)
    oil_art = next(a for a in articles if "Hormuz" in a.title)
    assert sports_art.event_id is None
    assert oil_art.event_id is not None


@pytest.mark.asyncio
async def test_rebuild_events_from_raw_preserves_llm_enrichment():
    """Verify Bronze->Silver rebuild re-clusters raw articles and preserves LLM fields by cluster_key."""
    from unittest.mock import AsyncMock, MagicMock
    from app.models.news import NewsArticle, NewsEvent
    from app.services.news_clusterer import make_cluster_key, tokenize_for_clustering
    from app.services.news_fetcher import NewsFetcher

    pub1 = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)
    pub2 = datetime(2026, 11, 19, 12, 0, tzinfo=timezone.utc)
    art1 = NewsArticle(
        id=1,
        url_hash="h1",
        url="https://a.com/1",
        source="gCaptain",
        source_tier=2,
        title="Oil tanker struck by missile in Strait of Hormuz",
        summary="Attack near Fujairah.",
        published_at=pub1,
    )
    art2 = NewsArticle(
        id=2,
        url_hash="h2",
        url="https://b.com/2",
        source="Reuters",
        source_tier=1,
        title="Crude oil tanker hit by missile attack in Strait of Hormuz",
        summary="Confirmed by UKMTO.",
        published_at=pub2,
    )
    art3 = NewsArticle(
        id=3,
        url_hash="h3",
        url="https://c.com/3",
        source="Sports",
        source_tier=3,
        title="Local basketball team wins championship",
        summary="",
        published_at=pub2,
    )

    expected_ckey = make_cluster_key(
        "chokepoint_attack_blockade",
        tokenize_for_clustering(f"{art1.title} {art1.summary}"),
        pub1,
    )
    prev_llm_event = NewsEvent(
        id=99,
        cluster_key=expected_ckey,
        headline=art1.title,
        event_type="chokepoint_attack_blockade",
        first_seen_at=pub1,
        market_aligned_at=pub1,
        llm_direction=0.95,
        llm_severity=0.92,
        llm_confidence=0.9,
        llm_estimated_mbpd=2.5,
        llm_rationale="Direct threat to Strait of Hormuz flows.",
        llm_enriched_at=pub2,
    )

    added_events = []
    next_id = 10
    call_idx = 0

    def exec_side_effect(*args, **kwargs):
        nonlocal call_idx
        call_idx += 1
        res = MagicMock()
        if call_idx == 1:
            # Query 1: existing LLM events
            res.scalars.return_value.all.return_value = [prev_llm_event]
        elif call_idx == 2:
            # Query 2: raw Bronze articles
            res.scalars.return_value.all.return_value = [art1, art2, art3]
        else:
            res.scalars.return_value.all.return_value = []
        return res

    async def flush_side_effect():
        nonlocal next_id
        for ev in added_events:
            if ev.id is None:
                ev.id = next_id
                next_id += 1

    mock_session = MagicMock()
    mock_session.add = MagicMock(side_effect=lambda obj: added_events.append(obj))
    mock_session.flush = AsyncMock(side_effect=flush_side_effect)
    mock_session.execute = AsyncMock(side_effect=exec_side_effect)
    mock_session.commit = AsyncMock()

    class MockAsyncSession:
        async def __aenter__(self):
            return mock_session
        async def __aexit__(self, exc_type, exc, tb):
            pass

    with patch("app.services.news_fetcher.async_session", return_value=MockAsyncSession()):
        fetcher = NewsFetcher()
        stats = await fetcher.rebuild_events_from_raw()

    assert stats["articles_replayed"] == 3
    assert stats["events_rebuilt"] == 1
    assert stats["articles_unmatched"] == 1
    assert stats["llm_preserved"] == 1
    assert art1.event_id == art2.event_id == added_events[0].id
    assert art3.event_id is None
    assert added_events[0].distinct_sources == 2
    assert added_events[0].llm_rationale == "Direct threat to Strait of Hormuz flows."


def test_agent_intel_routes_and_briefing_parser():
    """Verify /api/agent-intel endpoints register and GitHub Action briefing parser works cleanly."""
    import importlib.util
    from pathlib import Path
    from app.main import app

    agent_routes = [r.path for r in app.routes if "/agent-intel" in r.path]
    assert "/api/agent-intel/context" in agent_routes
    assert "/api/agent-intel/briefings" in agent_routes
    assert "/api/agent-intel/briefings/latest" in agent_routes

    script_path = Path(__file__).resolve().parents[3] / ".github" / "agents" / "run_market_briefing.py"
    spec = importlib.util.spec_from_file_location("run_market_briefing", script_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    raw_llm = (
        "### Executive Summary\n\nBrent rose +1.8% after Hormuz tensions.\n\n"
        'BRIEFING_JSON: {"market_bias": "bullish", "confidence": 0.78, '
        '"executive_summary": "Brent rose +1.8% after Hormuz tensions.", '
        '"key_points": ["Hormuz transits down 14%", "Brent +1.4% at +4h"], '
        '"watch_items": ["Monitor 24h Hormuz AIS recovery"]}'
    )
    md, structured = mod.parse_briefing_output(raw_llm)
    assert "BRIEFING_JSON" not in md
    assert structured["market_bias"] == "bullish"
    assert structured["confidence"] == 0.78
    assert len(structured["key_points"]) == 2
    assert len(structured["watch_items"]) == 1

    # Fallback when no LLM key is set must still produce valid markdown & structured payload
    fb_md, fb_struct = mod.build_deterministic_fallback({
        "analytics": {"total_vessels": 420, "in_transit": 250, "at_port": 170, "currently_dark": 12, "ongoing_sts": 3},
        "market_intel": {"prices": {"brent_usd": 76.5, "wti_usd": 72.1, "brent_change_1d_pct": 1.2}},
        "news_intel": {"events_24h": 8, "high_impact_events_24h": 2, "net_pressure_24h": 0.85, "pressure_label": "Bullish Supply Risk", "top_events": []},
    })
    assert "Executive Summary" in fb_md
    assert fb_struct["market_bias"] == "bullish"
    assert len(fb_struct["key_points"]) >= 2


@pytest.mark.asyncio
async def test_news_fetcher_429_cooldown_and_retry_after():
    from datetime import timedelta
    import httpx
    from app.services.news_fetcher import NewsFetcher

    fetcher = NewsFetcher()
    now = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
    req = httpx.Request("GET", "https://api.gdeltproject.org/api/v2/doc/doc")
    resp_429 = httpx.Response(429, headers={"Retry-After": "4500"}, request=req)
    exc = httpx.HTTPStatusError("429 Too Many Requests", request=req, response=resp_429)

    assert not fetcher._is_in_cooldown("gdelt:chokepoints", now)
    fetcher._handle_fetch_error("gdelt:chokepoints", "NewsFetcher GDELT[chokepoints]", exc, now)
    assert fetcher._is_in_cooldown("gdelt:chokepoints", now + timedelta(minutes=70))
    assert not fetcher._is_in_cooldown("gdelt:chokepoints", now + timedelta(minutes=80))


@pytest.mark.asyncio
async def test_news_fetcher_429_cooldown_short_retry_after():
    from datetime import timedelta
    from datetime import timezone
    from app.services.news_fetcher import NewsFetcher
    import httpx

    fetcher = NewsFetcher()
    now = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
    req = httpx.Request("GET", "https://api.gdeltproject.org/api/v2/doc/doc")
    resp_429 = httpx.Response(429, headers={"Retry-After": "10"}, request=req)
    exc = httpx.HTTPStatusError("429 Too Many Requests", request=req, response=resp_429)

    assert not fetcher._is_in_cooldown("gdelt:chokepoints", now)
    fetcher._handle_fetch_error("gdelt:chokepoints", "NewsFetcher GDELT[chokepoints]", exc, now)
    # With the fix, cooldown should be at most RATE_LIMIT_COOLDOWN_SECONDS (5 minutes)
    assert fetcher._is_in_cooldown("gdelt:chokepoints", now + timedelta(minutes=4))
    assert not fetcher._is_in_cooldown("gdelt:chokepoints", now + timedelta(minutes=6))


@pytest.mark.asyncio
async def test_news_fetcher_403_sets_6h_cooldown():
    from datetime import timedelta
    import httpx
    from app.services.news_fetcher import NewsFetcher

    fetcher = NewsFetcher()
    now = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
    req = httpx.Request("GET", "https://www.rigzone.com/news/rss/rigzone_latest.aspx")
    resp_403 = httpx.Response(403, request=req)
    exc = httpx.HTTPStatusError("403 Forbidden", request=req, response=resp_403)

    fetcher._handle_fetch_error("rss:Rigzone", "NewsFetcher RSS[Rigzone]", exc, now)
    assert fetcher._is_in_cooldown("rss:Rigzone", now + timedelta(hours=5))
    assert not fetcher._is_in_cooldown("rss:Rigzone", now + timedelta(hours=7))


