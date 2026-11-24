import { test, expect } from "@playwright/test";

const MOCK_FORECAST = {
  symbol: "BZ=F",
  symbol_label: "Brent Crude",
  as_of: "2026-11-24T18:00:00Z",
  current_price_usd: 87.2,
  price_date: "2026-11-24",
  daily_vol_pct: 2.1,
  composite_score: 48.5,
  primary_horizon: "3d",
  verdict: "Bullish",
  direction: "bullish",
  headline_summary:
    "BULLISH (+1.65% over 3d (~$88.64 vs $87.20 now)) — led by news analog memory.",
  horizons: {
    "24h": {
      horizon: "24h",
      verdict: "Bullish",
      direction: "bullish",
      predicted_return_pct: 0.92,
      predicted_price_usd: 88.0,
      price_low_usd: 86.45,
      price_high_usd: 89.55,
      confidence_pct: 68.0,
      pillar_return_pct: 0.85,
      ml_return_pct: 1.05,
      ml_weight: 0.35,
      backtest_hit_rate_pct: 61.5,
      backtest_mae_pct: 1.42,
      backtest_samples: 26,
    },
    "3d": {
      horizon: "3d",
      verdict: "Bullish",
      direction: "bullish",
      predicted_return_pct: 1.65,
      predicted_price_usd: 88.64,
      price_low_usd: 85.94,
      price_high_usd: 91.34,
      confidence_pct: 71.0,
      pillar_return_pct: 1.5,
      ml_return_pct: 1.9,
      ml_weight: 0.35,
      backtest_hit_rate_pct: 64.0,
      backtest_mae_pct: 2.15,
      backtest_samples: 25,
    },
    "7d": {
      horizon: "7d",
      verdict: "Bullish",
      direction: "bullish",
      predicted_return_pct: 2.1,
      predicted_price_usd: 89.03,
      price_low_usd: 84.9,
      price_high_usd: 93.16,
      confidence_pct: 65.0,
      pillar_return_pct: 2.0,
      ml_return_pct: 2.3,
      ml_weight: 0.3,
      backtest_hit_rate_pct: 59.0,
      backtest_mae_pct: 3.1,
      backtest_samples: 22,
    },
  },
  pillars: [
    {
      key: "news_analogs",
      label: "News Analog Memory",
      score: 62.0,
      weight: 0.35,
      direction: "bullish",
      summary: "Active headlines match 240 historical precedents, implying +1.42% (24h).",
      metrics: ["12 active events (48h) matched against 240 historical events"],
    },
    {
      key: "chokepoint_flow",
      label: "Chokepoints & Tanker Flows",
      score: 45.0,
      weight: 0.3,
      direction: "bullish",
      summary: "Hormuz transit drop and crisis pressure signal elevated supply friction.",
      metrics: ["Hormuz transits: 2 vs 14.0 30d avg"],
    },
    {
      key: "dark_fleet_supply",
      label: "Dark Fleet & Supply Chain",
      score: 30.0,
      weight: 0.2,
      direction: "bullish",
      summary: "Dark-fleet & STS activity is elevated above trailing norms.",
      metrics: ["Dark fleet: 5013 vs 2243 30d avg"],
    },
    {
      key: "market_momentum",
      label: "Price Momentum & Regime",
      score: -10.0,
      weight: 0.15,
      direction: "neutral",
      summary: "Brent Crude is trading sideways (-1.2% over 7d).",
      metrics: ["3d trend: -0.5% · 7d trend: -1.2%"],
    },
  ],
  top_drivers: [
    {
      source_type: "news",
      title: "Three tankers explode in Strait of Hormuz",
      impact_direction: "bullish",
      expected_move_pct: 1.42,
      horizon: "24h",
      confidence_pct: 68.0,
      detail: "Based on 6 similar past events (avg 54% match), Brent is projected +1.42% over the next 24h.",
    },
  ],
  active_news_count_48h: 12,
  analog_memory_size: 240,
  latest_briefing_bias: "bullish",
  latest_briefing_summary: "Supply risk elevated in Strait of Hormuz.",
};

const MOCK_NEWS_EVENTS = [
  {
    id: 197,
    headline: "Three tankers explode in Strait of Hormuz",
    summary: "Security alert issued after multiple oil tankers report explosions.",
    event_type: "chokepoint_attack_blockade",
    event_type_label: "Chokepoint Attack / Blockade",
    chokepoints: ["Strait of Hormuz"],
    entities: ["Iran"],
    primary_region: "Strait of Hormuz",
    first_seen_at: "2026-11-24T16:00:00Z",
    market_aligned_at: "2026-11-24T16:00:00Z",
    article_count: 3,
    distinct_sources: 3,
    max_source_tier: 1,
    novelty_score: 0.9,
    effective_direction: 0.85,
    effective_severity: 0.92,
    prior_rank_score: 82.5,
    learned_impact_score: 0.61,
    llm_enriched: false,
    llm_confidence: null,
    llm_estimated_mbpd: null,
    llm_rationale: null,
    brent_impact: null,
    wti_impact: null,
    prediction: {
      symbol: "BZ=F",
      peak_horizon: "24h",
      direction: "bullish",
      expected_return_pct: 1.42,
      range_low_pct: 0.35,
      range_high_pct: 2.49,
      confidence_pct: 68.0,
      analog_count: 6,
      avg_similarity: 0.54,
      similarity_mode: "hybrid_tfidf_structured",
      narrative:
        "Based on 6 similar past events (avg 54% match), Brent is projected +1.42% over the next 24h (83% directional agreement).",
      horizons: {},
      analogs: [
        {
          event_id: 42,
          headline: "Tanker struck by drone near Strait of Hormuz",
          event_type: "chokepoint_attack_blockade",
          event_type_label: "Chokepoint Attack / Blockade",
          first_seen_at: "2026-10-12T10:00:00Z",
          similarity: 0.62,
          ret_4h_pct: 0.75,
          ret_24h_pct: 1.88,
          ret_3d_pct: 2.3,
          ret_7d_pct: 1.15,
        },
      ],
    },
    articles: [],
  },
];

test.describe("Bottom-Line Oil Price Predictor & News Analog Memory UI", () => {
  test("Dashboard displays Bottom-Line Forecast card and Top Market-Moving News", async ({ page }) => {
    await page.route("**/api/analytics/dashboard*", async (route) => {
      await route.fulfill({
        json: {
          fleet: { total_vessels: 9000, in_transit: 5400, at_port: 3600, currently_dark: 420, loaded: 2800, ballast: 2600 },
          active_alerts: 12,
          ongoing_sts_events: 18,
          congested_chokepoints: 2,
          recent_cargo_events: [],
          daily_volume_barrels: 14500000,
        },
      });
    });
    await page.route("**/api/forecast/bottom-line*", async (route) => {
      await route.fulfill({ json: MOCK_FORECAST });
    });
    await page.route("**/api/news/events*", async (route) => {
      await route.fulfill({ json: MOCK_NEWS_EVENTS });
    });

    await page.goto("/");
    const card = page.getByTestId("bottom-line-card");
    await expect(card).toBeVisible();
    await expect(card).toContainText("BULLISH");
    await expect(card).toContainText("+1.65%");
    await expect(card).toContainText("$88.64");
    await expect(page.getByText("Three tankers explode in Strait of Hormuz").first()).toBeVisible();
  });

  test("News page displays Predicted Oil Impact banner and expands Historical Memory analogs", async ({ page }) => {
    await page.route("**/api/news/summary*", async (route) => {
      await route.fulfill({
        json: {
          events_24h: 12,
          high_impact_events_24h: 3,
          total_events_30d: 240,
          net_pressure_24h: 4.2,
          pressure_label: "Bullish Supply Risk",
          top_event_24h: MOCK_NEWS_EVENTS[0],
          strongest_learned_driver: null,
          llm_enabled: false,
          llm_provider: null,
          scoring_mode: "Local Rules + Empirical Lag (LLM Off)",
        },
      });
    });
    await page.route("**/api/news/impact-matrix*", async (route) => {
      await route.fulfill({ json: { symbol: "BZ=F", as_of_date: "2026-11-24", horizon_order: [], rows: [] } });
    });
    await page.route("**/api/news/events*", async (route) => {
      await route.fulfill({ json: MOCK_NEWS_EVENTS });
    });
    await page.route("**/api/agent-intel/briefings/latest*", async (route) => {
      await route.fulfill({ status: 404, json: { detail: "none" } });
    });

    await page.goto("/news");
    const banner = page.getByTestId("event-prediction-banner").first();
    await expect(banner).toBeVisible();
    await expect(banner).toContainText("Brent +1.42% (24h)");
    await expect(banner).toContainText("6 analogs");

    await page.getByRole("button", { name: /View analogs/i }).first().click();
    await expect(page.getByText("Tanker struck by drone near Strait of Hormuz")).toBeVisible();
  });
});
