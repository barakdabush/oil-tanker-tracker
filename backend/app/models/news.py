"""News & Event Intelligence database models.

Stores:
- ``oil_prices_intraday``: 1h OHLCV bars for Brent (BZ=F) and WTI (CL=F) + trailing 20d volatility.
- ``news_events``: Canonical deduplicated/clustered real-world oil & maritime events.
- ``news_articles``: Raw ingested articles linked to their parent ``news_events`` cluster.
- ``news_event_price_impacts``: Materialized multi-horizon price reactions (-24h to +14d) per event.
- ``news_impact_priors``: Walk-forward Empirical Bayes learned impact & lag matrix per category.
"""

from sqlalchemy import (
    BigInteger,
    Column,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP

from app.database import Base


class OilPriceIntraday(Base):
    """Hourly OHLCV bars for Brent (BZ=F) and WTI (CL=F) futures."""

    __tablename__ = "oil_prices_intraday"

    symbol = Column(String(16), primary_key=True)
    bar_time = Column(TIMESTAMP(timezone=True), primary_key=True)
    interval = Column(String(8), nullable=False, default="1h")
    open = Column(Float)
    high = Column(Float)
    low = Column(Float)
    close = Column(Float, nullable=False)
    volume = Column(Float)
    realized_vol_20d = Column(Float)
    fetched_at = Column(TIMESTAMP(timezone=True), server_default=text("NOW()"))

    __table_args__ = (
        Index("idx_oil_prices_intraday_bar_time", "bar_time"),
    )


class NewsEvent(Base):
    """Canonical clustered real-world oil, tanker, chokepoint, or macro event."""

    __tablename__ = "news_events"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    cluster_key = Column(String(64), nullable=False, index=True)
    headline = Column(Text, nullable=False)
    summary = Column(Text)
    event_type = Column(String(48), nullable=False, index=True)
    chokepoints = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    entities = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    primary_region = Column(String(64), nullable=False, default="global")

    first_seen_at = Column(TIMESTAMP(timezone=True), nullable=False, index=True)
    market_aligned_at = Column(TIMESTAMP(timezone=True), nullable=False, index=True)
    last_updated_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"))

    article_count = Column(Integer, nullable=False, default=1)
    distinct_sources = Column(Integer, nullable=False, default=1)
    max_source_tier = Column(Integer, nullable=False, default=3)  # 1 = highest credibility
    novelty_score = Column(Float, nullable=False, default=1.0)

    # Local rule-based baseline scoring (always populated, zero cost)
    rule_direction = Column(Float, nullable=False, default=0.0)  # [-1.0 bearish .. +1.0 bullish]
    rule_severity = Column(Float, nullable=False, default=0.3)   # [0.0 .. 1.0]

    # Optional LLM enrichment (nullable; never required)
    llm_direction = Column(Float)
    llm_severity = Column(Float)
    llm_confidence = Column(Float)
    llm_estimated_mbpd = Column(Float)
    llm_rationale = Column(Text)
    llm_enriched_at = Column(TIMESTAMP(timezone=True))

    # Effective combined signals & ranking scores
    effective_direction = Column(Float, nullable=False, default=0.0)
    effective_severity = Column(Float, nullable=False, default=0.3)
    prior_rank_score = Column(Float, nullable=False, default=0.0, index=True)  # 0..100
    learned_impact_score = Column(Float)  # empirical expected z-score magnitude
    embedding = Column(JSONB, nullable=True)  # optional dense semantic embedding vector

    created_at = Column(TIMESTAMP(timezone=True), server_default=text("NOW()"))


class NewsArticle(Base):
    """Raw deduplicated news article linked to a canonical NewsEvent."""

    __tablename__ = "news_articles"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    url_hash = Column(String(64), nullable=False, unique=True, index=True)
    url = Column(Text, nullable=False)
    source = Column(String(128), nullable=False)
    source_tier = Column(Integer, nullable=False, default=3)
    title = Column(Text, nullable=False)
    summary = Column(Text)
    published_at = Column(TIMESTAMP(timezone=True), nullable=False, index=True)
    fetched_at = Column(TIMESTAMP(timezone=True), server_default=text("NOW()"))
    gdelt_tone = Column(Float)
    event_id = Column(
        BigInteger,
        ForeignKey("news_events.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )


class NewsEventPriceImpact(Base):
    """Materialized multi-horizon Brent/WTI price reaction around a NewsEvent."""

    __tablename__ = "news_event_price_impacts"

    event_id = Column(
        BigInteger,
        ForeignKey("news_events.id", ondelete="CASCADE"),
        primary_key=True,
    )
    symbol = Column(String(16), primary_key=True)  # BZ=F or CL=F

    price_t0 = Column(Float)
    vol_20d_t0 = Column(Float)

    # Pre-event drift (priced-in / leak detection)
    ret_pre_24h = Column(Float)
    ret_pre_4h = Column(Float)

    # Post-event raw log-returns
    ret_1h = Column(Float)
    ret_4h = Column(Float)
    ret_24h = Column(Float)
    ret_3d = Column(Float)
    ret_7d = Column(Float)
    ret_14d = Column(Float)

    # Post-event volatility-normalized z-scores
    z_1h = Column(Float)
    z_4h = Column(Float)
    z_24h = Column(Float)
    z_3d = Column(Float)
    z_7d = Column(Float)
    z_14d = Column(Float)

    max_computed_horizon = Column(String(16))
    updated_at = Column(TIMESTAMP(timezone=True), server_default=text("NOW()"))


class NewsImpactPrior(Base):
    """Walk-forward Empirical Bayes learned impact & lag profile per event category."""

    __tablename__ = "news_impact_priors"

    as_of_date = Column(Date, primary_key=True)
    event_type = Column(String(48), primary_key=True)
    region_scope = Column(String(64), primary_key=True, default="global")
    symbol = Column(String(16), primary_key=True)   # BZ=F or CL=F
    horizon = Column(String(16), primary_key=True)  # pre_24h, pre_4h, 1h, 4h, 24h, 3d, 7d, 14d

    sample_count = Column(Integer, nullable=False, default=0)
    mean_ret = Column(Float)
    median_ret = Column(Float)
    mean_z = Column(Float)
    hit_rate = Column(Float)       # fraction of events where sign(ret) == expected direction
    t_stat = Column(Float)
    shrunk_weight = Column(Float)  # Empirical Bayes shrunk weight: n/(n+k) * mean_z
    half_life_hours = Column(Float)
    computed_at = Column(TIMESTAMP(timezone=True), server_default=text("NOW()"))


class AIMarketBriefing(Base):
    """Stored AI agent market intelligence briefings generated by GitHub Actions or external agents."""

    __tablename__ = "ai_market_briefings"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    window_days = Column(Integer, nullable=False, default=14)
    model_used = Column(String(128))
    market_bias = Column(String(24), nullable=False, default="neutral")  # bullish | bearish | neutral | mixed
    confidence = Column(Float)
    executive_summary = Column(Text, nullable=False)
    key_points = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    watch_items = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    full_markdown = Column(Text)
    context_snapshot = Column(JSONB)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, server_default=text("NOW()"), index=True)

