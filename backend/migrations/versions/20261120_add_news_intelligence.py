"""Add news & event intelligence tables, intraday oil prices, and snapshot news features.

Revision ID: add_news_intelligence
Revises: add_strait_risk_daily
Create Date: 2026-11-20 04:45:00.000000

"""
from typing import Sequence, Union
from alembic import op


revision: str = "add_news_intelligence"
down_revision: Union[str, None] = "add_strait_risk_daily"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Intraday oil price bars (1h OHLCV + 20d realized volatility)
    op.execute("""
        CREATE TABLE IF NOT EXISTS oil_prices_intraday (
            symbol VARCHAR(16) NOT NULL,
            bar_time TIMESTAMPTZ NOT NULL,
            interval VARCHAR(8) NOT NULL DEFAULT '1h',
            open FLOAT,
            high FLOAT,
            low FLOAT,
            close FLOAT NOT NULL,
            volume FLOAT,
            realized_vol_20d FLOAT,
            fetched_at TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (symbol, bar_time)
        )
    """)
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_oil_prices_intraday_bar_time "
        "ON oil_prices_intraday (bar_time DESC)"
    )
    # Convert to TimescaleDB hypertable if TimescaleDB is installed
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'timescaledb') THEN
                PERFORM create_hypertable('oil_prices_intraday', 'bar_time', if_not_exists => TRUE);
            END IF;
        EXCEPTION WHEN OTHERS THEN
            RAISE NOTICE 'Skipping hypertable conversion for oil_prices_intraday: %', SQLERRM;
        END $$;
    """)

    # 2. Canonical clustered news events
    op.execute("""
        CREATE TABLE IF NOT EXISTS news_events (
            id BIGSERIAL PRIMARY KEY,
            cluster_key VARCHAR(64) NOT NULL,
            headline TEXT NOT NULL,
            summary TEXT,
            event_type VARCHAR(48) NOT NULL,
            chokepoints JSONB NOT NULL DEFAULT '[]'::jsonb,
            entities JSONB NOT NULL DEFAULT '[]'::jsonb,
            primary_region VARCHAR(64) NOT NULL DEFAULT 'global',
            first_seen_at TIMESTAMPTZ NOT NULL,
            market_aligned_at TIMESTAMPTZ NOT NULL,
            last_updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            article_count INT NOT NULL DEFAULT 1,
            distinct_sources INT NOT NULL DEFAULT 1,
            max_source_tier INT NOT NULL DEFAULT 3,
            novelty_score FLOAT NOT NULL DEFAULT 1.0,
            rule_direction FLOAT NOT NULL DEFAULT 0.0,
            rule_severity FLOAT NOT NULL DEFAULT 0.3,
            llm_direction FLOAT,
            llm_severity FLOAT,
            llm_confidence FLOAT,
            llm_estimated_mbpd FLOAT,
            llm_rationale TEXT,
            llm_enriched_at TIMESTAMPTZ,
            effective_direction FLOAT NOT NULL DEFAULT 0.0,
            effective_severity FLOAT NOT NULL DEFAULT 0.3,
            prior_rank_score FLOAT NOT NULL DEFAULT 0.0,
            learned_impact_score FLOAT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_news_events_cluster_key ON news_events (cluster_key)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_news_events_event_type ON news_events (event_type)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_news_events_first_seen ON news_events (first_seen_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_news_events_market_aligned ON news_events (market_aligned_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_news_events_prior_rank ON news_events (prior_rank_score DESC)")

    # 3. Raw deduplicated news articles
    op.execute("""
        CREATE TABLE IF NOT EXISTS news_articles (
            id BIGSERIAL PRIMARY KEY,
            url_hash VARCHAR(64) NOT NULL UNIQUE,
            url TEXT NOT NULL,
            source VARCHAR(128) NOT NULL,
            source_tier INT NOT NULL DEFAULT 3,
            title TEXT NOT NULL,
            summary TEXT,
            published_at TIMESTAMPTZ NOT NULL,
            fetched_at TIMESTAMPTZ DEFAULT NOW(),
            gdelt_tone FLOAT,
            event_id BIGINT REFERENCES news_events(id) ON DELETE SET NULL
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_news_articles_published ON news_articles (published_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_news_articles_event_id ON news_articles (event_id)")

    # 4. Materialized multi-horizon event price impacts
    op.execute("""
        CREATE TABLE IF NOT EXISTS news_event_price_impacts (
            event_id BIGINT NOT NULL REFERENCES news_events(id) ON DELETE CASCADE,
            symbol VARCHAR(16) NOT NULL,
            price_t0 FLOAT,
            vol_20d_t0 FLOAT,
            ret_pre_24h FLOAT,
            ret_pre_4h FLOAT,
            ret_1h FLOAT,
            ret_4h FLOAT,
            ret_24h FLOAT,
            ret_3d FLOAT,
            ret_7d FLOAT,
            ret_14d FLOAT,
            z_1h FLOAT,
            z_4h FLOAT,
            z_24h FLOAT,
            z_3d FLOAT,
            z_7d FLOAT,
            z_14d FLOAT,
            max_computed_horizon VARCHAR(16),
            updated_at TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (event_id, symbol)
        )
    """)

    # 5. Walk-forward Empirical Bayes learned impact priors
    op.execute("""
        CREATE TABLE IF NOT EXISTS news_impact_priors (
            as_of_date DATE NOT NULL,
            event_type VARCHAR(48) NOT NULL,
            region_scope VARCHAR(64) NOT NULL DEFAULT 'global',
            symbol VARCHAR(16) NOT NULL,
            horizon VARCHAR(16) NOT NULL,
            sample_count INT NOT NULL DEFAULT 0,
            mean_ret FLOAT,
            median_ret FLOAT,
            mean_z FLOAT,
            hit_rate FLOAT,
            t_stat FLOAT,
            shrunk_weight FLOAT,
            half_life_hours FLOAT,
            computed_at TIMESTAMPTZ DEFAULT NOW(),
            PRIMARY KEY (as_of_date, event_type, region_scope, symbol, horizon)
        )
    """)

    # 6. Stored AI agent market briefings
    op.execute("""
        CREATE TABLE IF NOT EXISTS ai_market_briefings (
            id BIGSERIAL PRIMARY KEY,
            window_days INT NOT NULL DEFAULT 14,
            model_used VARCHAR(128),
            market_bias VARCHAR(24) NOT NULL DEFAULT 'neutral',
            confidence FLOAT,
            executive_summary TEXT NOT NULL,
            key_points JSONB NOT NULL DEFAULT '[]'::jsonb,
            watch_items JSONB NOT NULL DEFAULT '[]'::jsonb,
            full_markdown TEXT,
            context_snapshot JSONB,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_ai_market_briefings_created ON ai_market_briefings (created_at DESC)")

    # 7. Extend oil_market_snapshots with daily news + AIS x News interaction features
    op.execute("""
        ALTER TABLE oil_market_snapshots
            ADD COLUMN IF NOT EXISTS news_event_count_24h INT,
            ADD COLUMN IF NOT EXISTS news_max_rank_24h FLOAT,
            ADD COLUMN IF NOT EXISTS news_net_impact_1d FLOAT,
            ADD COLUMN IF NOT EXISTS news_net_impact_7d FLOAT,
            ADD COLUMN IF NOT EXISTS news_supply_risk_score FLOAT,
            ADD COLUMN IF NOT EXISTS news_sanctions_pressure_7d FLOAT,
            ADD COLUMN IF NOT EXISTS news_opec_signal_7d FLOAT,
            ADD COLUMN IF NOT EXISTS news_hormuz_x_transit_drop FLOAT,
            ADD COLUMN IF NOT EXISTS news_sanctions_x_dark_fleet FLOAT
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE oil_market_snapshots
            DROP COLUMN IF EXISTS news_event_count_24h,
            DROP COLUMN IF EXISTS news_max_rank_24h,
            DROP COLUMN IF EXISTS news_net_impact_1d,
            DROP COLUMN IF EXISTS news_net_impact_7d,
            DROP COLUMN IF EXISTS news_supply_risk_score,
            DROP COLUMN IF EXISTS news_sanctions_pressure_7d,
            DROP COLUMN IF EXISTS news_opec_signal_7d,
            DROP COLUMN IF EXISTS news_hormuz_x_transit_drop,
            DROP COLUMN IF EXISTS news_sanctions_x_dark_fleet
    """)
    op.execute("DROP TABLE IF EXISTS ai_market_briefings")
    op.execute("DROP TABLE IF EXISTS news_impact_priors")
    op.execute("DROP TABLE IF EXISTS news_event_price_impacts")
    op.execute("DROP TABLE IF EXISTS news_articles")
    op.execute("DROP TABLE IF EXISTS news_events")
    op.execute("DROP TABLE IF EXISTS oil_prices_intraday")
