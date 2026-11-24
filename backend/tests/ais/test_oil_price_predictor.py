"""Unit tests for news classification fixes, similarity analog memory, and Bottom-Line Oil Price Predictor."""

from datetime import date, datetime, timedelta, timezone
import math

from app.main import app
from app.services.news_clusterer import classify_article
from app.services.news_predictor import HistoricalEventRecord, NewsAnalogMemory
from app.services.oil_price_predictor import (
    compute_realized_daily_vol_pct,
    run_walkforward_ridge,
)


def test_news_clusterer_filters_noise_and_avoids_hormuz_false_attack():
    # Educational/FAQ article should be rejected as non-market noise
    faq = classify_article("How does EIA compare different types of energy?")
    assert not faq.relevant

    # Routine energy brief mentioning Strait of Hormuz should be rejected
    brief = classify_article("Energy briefs–oil traffic in the Strait of Hormuz - Oklahoma Energy Today")
    assert not brief.relevant

    # Genuine Hormuz tanker explosion MUST still be classified as chokepoint_attack_blockade (bullish)
    attack = classify_article("Three tankers explode in Strait of Hormuz after missile strike")
    assert attack.relevant
    assert attack.event_type == "chokepoint_attack_blockade"
    assert attack.direction > 0.5

    # Supply increase headline should NOT be tagged as refinery outage
    supply_inc = classify_article("Saudi Arabia Sees Smaller Economic Slump as More Oil Reaches Market")
    assert supply_inc.relevant
    assert supply_inc.event_type != "refinery_pipeline_outage"
    assert supply_inc.direction < 0


def test_news_analog_memory_matches_similar_events_with_strict_anti_leakage():
    t0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    records = [
        # Past similar Hormuz tanker attack events (+2.0% to +2.8% over 24h)
        HistoricalEventRecord(
            id=1,
            headline="Oil tanker struck by missile near Strait of Hormuz",
            summary="Tanker caught fire in Strait of Hormuz causing shipping halt.",
            event_type="chokepoint_attack_blockade",
            chokepoints=("Strait of Hormuz",),
            entities=("Iran",),
            first_seen_at=t0,
            market_aligned_at=t0,
            effective_direction=0.85,
            effective_severity=0.9,
            prior_rank_score=75.0,
            returns={"4h": 0.011, "24h": 0.024, "3d": 0.031, "7d": 0.018},
            vol_20d=0.02,
        ),
        HistoricalEventRecord(
            id=2,
            headline="Tanker catches fire in Strait of Hormuz after drone attack",
            summary="Maritime incident disrupts crude transit through Hormuz.",
            event_type="chokepoint_attack_blockade",
            chokepoints=("Strait of Hormuz",),
            entities=("Iran",),
            first_seen_at=t0 + timedelta(days=5),
            market_aligned_at=t0 + timedelta(days=5),
            effective_direction=0.85,
            effective_severity=0.88,
            prior_rank_score=72.0,
            returns={"4h": 0.009, "24h": 0.020, "3d": 0.025, "7d": 0.012},
            vol_20d=0.02,
        ),
        # Unrelated OPEC hike (-2.5% over 24h)
        HistoricalEventRecord(
            id=3,
            headline="OPEC+ agrees to boost crude output and unwind voluntary cuts",
            summary="Producers plan to add barrels next quarter.",
            event_type="opec_production_decision",
            chokepoints=(),
            entities=("OPEC",),
            first_seen_at=t0 + timedelta(days=8),
            market_aligned_at=t0 + timedelta(days=8),
            effective_direction=-0.8,
            effective_severity=0.75,
            prior_rank_score=70.0,
            returns={"4h": -0.012, "24h": -0.025, "3d": -0.030, "7d": -0.022},
            vol_20d=0.02,
        ),
        # Target new Hormuz attack event (arrives at day 15)
        HistoricalEventRecord(
            id=4,
            headline="Oil tanker struck by drone in Strait of Hormuz",
            summary="Vessel on fire near Strait of Hormuz.",
            event_type="chokepoint_attack_blockade",
            chokepoints=("Strait of Hormuz",),
            entities=("Iran",),
            first_seen_at=t0 + timedelta(days=15),
            market_aligned_at=t0 + timedelta(days=15),
            effective_direction=0.85,
            effective_severity=0.9,
            prior_rank_score=78.0,
            returns={},
            vol_20d=0.02,
        ),
        # Future event at day 20 — MUST NEVER be leaked into target event #4's analogs
        HistoricalEventRecord(
            id=5,
            headline="Oil tanker struck by drone in Strait of Hormuz future leak test",
            summary="Future event that must be excluded.",
            event_type="chokepoint_attack_blockade",
            chokepoints=("Strait of Hormuz",),
            entities=("Iran",),
            first_seen_at=t0 + timedelta(days=20),
            market_aligned_at=t0 + timedelta(days=20),
            effective_direction=0.85,
            effective_severity=0.9,
            prior_rank_score=80.0,
            returns={"4h": -0.10, "24h": -0.15, "3d": -0.15, "7d": -0.15},
            vol_20d=0.02,
        ),
    ]

    memory = NewsAnalogMemory(records=records)
    pred = memory.predict_for_event(
        records[3],
        symbol="BZ=F",
        reference_time=t0 + timedelta(days=15, hours=1),
    )

    assert pred.direction == "bullish"
    assert pred.expected_return_pct > 0.5
    assert pred.analog_count >= 2
    assert pred.similarity_mode == "hybrid_tfidf_structured"
    matched_ids = [a.event_id for a in pred.analogs]
    assert 1 in matched_ids and 2 in matched_ids
    assert 5 not in matched_ids  # strict anti-leakage!
    assert "Brent" in pred.narrative


def test_news_analog_memory_dense_embedding_mode():
    t0 = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
    past = HistoricalEventRecord(
        id=10,
        headline="Completely different wording about Gulf shipping",
        summary="",
        event_type="geopolitical_escalation",
        chokepoints=(),
        entities=(),
        first_seen_at=t0,
        market_aligned_at=t0,
        effective_direction=0.5,
        effective_severity=0.6,
        prior_rank_score=60.0,
        embedding=[1.0, 0.0, 0.0, 0.0, 0.5, 0.5, 0.0, 0.0],
        returns={"24h": 0.018},
    )
    target = HistoricalEventRecord(
        id=11,
        headline="Unrelated lexical terms",
        summary="",
        event_type="geopolitical_escalation",
        chokepoints=(),
        entities=(),
        first_seen_at=t0 + timedelta(days=5),
        market_aligned_at=t0 + timedelta(days=5),
        effective_direction=0.5,
        effective_severity=0.6,
        prior_rank_score=60.0,
        embedding=[0.98, 0.02, 0.0, 0.0, 0.48, 0.52, 0.0, 0.0],
        returns={},
    )
    memory = NewsAnalogMemory(records=[past, target])
    pred = memory.predict_for_event(target, symbol="BZ=F", reference_time=t0 + timedelta(days=6))
    assert pred.similarity_mode == "dense_embedding"
    assert pred.analog_count == 1
    assert pred.expected_return_pct > 0


def test_walkforward_ridge_and_vol_and_routes():
    # Synthetic 45-day series where rising hormuz_crisis_pressure predicts positive Brent return
    base_d = date(2026, 9, 1)
    rows = []
    price = 80.0
    for i in range(45):
        pressure = 50.0 + 30.0 * math.sin(i / 4.0)
        news_sig = 5.0 * math.sin(i / 4.0)
        rows.append({
            "snapshot_date": base_d + timedelta(days=i),
            "brent_close_usd": round(price, 2),
            "strait_of_hormuz_transits": max(1, int(15 - pressure / 10)),
            "dark_vessels_count": int(1000 + pressure * 10),
            "hormuz_crisis_pressure": pressure,
            "news_net_impact_1d": news_sig,
        })
        # Next day's return responds positively to today's pressure
        ret = 0.008 * math.sin(i / 4.0)
        price *= math.exp(ret)

    vol = compute_realized_daily_vol_pct([r["brent_close_usd"] for r in rows])
    assert 0.75 <= vol <= 6.5

    pred_pct, hit_rate, mae, samples = run_walkforward_ridge(
        snapshot_dicts=rows,
        price_key="brent_close_usd",
        horizon_days=1,
    )
    assert pred_pct is not None
    assert samples >= 20
    assert hit_rate is not None and hit_rate > 55.0
    assert mae is not None and mae >= 0.0

    # Verify new API routes are registered on FastAPI app
    paths = {r.path for r in app.routes}
    assert "/api/forecast/bottom-line" in paths
    assert "/api/forecast/backfill-macro" in paths
    assert "/api/news/events/{event_id}/prediction" in paths
    assert "/api/news/backfill" in paths
    assert "/api/news/score-impacts" in paths
    assert "/api/news/backtest" in paths


def test_crack_spread_and_walkforward_gbt_model():
    from app.services.gbt_oil_model import run_walkforward_gbt
    from app.services.macro_market_fetcher import compute_crack_spread_321

    # 3-2-1 crack spread: ((2*2.50*42 + 1*2.80*42) / 3) - 80.0 = 109.20 - 80.0 = 29.20 $/bbl
    spread = compute_crack_spread_321(rbob_gal=2.50, heating_oil_gal=2.80, wti_bbl=80.0)
    assert spread == 29.2
    assert compute_crack_spread_321(None, 2.80, 80.0) is None

    # Synthetic 320-day series where rising crack spread predicts positive 3d Brent returns
    base_d = date(2025, 1, 1)
    rows = []
    price = 75.0
    for i in range(320):
        wave = math.sin(i / 6.0)
        crack = 22.0 + 6.0 * wave
        rows.append({
            "date": base_d + timedelta(days=i),
            "brent_close_usd": round(price, 2),
            "wti_close_usd": round(price - 4.0, 2),
            "crack_spread_321_usd": round(crack, 2),
            "brent_wti_spread_usd": 4.0,
            "dxy_close": round(103.0 - 2.0 * wave, 2),
            "vix_close": round(18.0 + 3.0 * abs(wave), 2),
            "tanker_etf_close": round(20.0 + 3.0 * wave, 2),
            "energy_etf_close": round(90.0 + 4.0 * wave, 2),
            "news_dir_1d": round(0.8 * wave, 3),
            "news_shock_count_1d": 1 if wave > 0.5 else 0,
        })
        price *= math.exp(0.007 * wave)

    res = run_walkforward_gbt(
        daily_rows=rows,
        price_key="brent_close_usd",
        horizon_days=3,
        min_train_rows=180,
        refit_every=25,
    )
    assert res.latest_pred_pct is not None
    assert res.q15_pct is not None and res.q85_pct is not None
    assert res.q15_pct <= res.q85_pct
    assert res.samples >= 100
    assert res.hit_rate_pct is not None and res.hit_rate_pct > 55.0

    from app.services.gbt_oil_model import run_walkforward_wave_classifier

    wres = run_walkforward_wave_classifier(
        daily_rows=rows,
        price_key="brent_close_usd",
        horizon_days=7,
        min_train_rows=180,
        refit_every=25,
    )
    assert wres.up_wave_prob_pct is not None and wres.down_wave_prob_pct is not None
    assert wres.wave_state in ("Bullish Wave", "Bearish Wave", "Range-Bound")
    assert wres.signal_samples >= 20
    assert wres.signal_hit_rate_pct is not None and wres.signal_hit_rate_pct > 55.0
