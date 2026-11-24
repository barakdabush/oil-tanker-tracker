"""Pydantic schemas for News & Event Intelligence API."""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel


class NewsArticleItem(BaseModel):
    id: int
    source: str
    source_tier: int
    title: str
    url: str
    published_at: datetime
    gdelt_tone: Optional[float] = None

    model_config = {"from_attributes": True}


class EventPriceReaction(BaseModel):
    symbol: str
    price_t0: Optional[float] = None
    ret_pre_24h: Optional[float] = None
    ret_pre_4h: Optional[float] = None
    ret_1h: Optional[float] = None
    ret_4h: Optional[float] = None
    ret_24h: Optional[float] = None
    ret_3d: Optional[float] = None
    ret_7d: Optional[float] = None
    ret_14d: Optional[float] = None
    z_1h: Optional[float] = None
    z_4h: Optional[float] = None
    z_24h: Optional[float] = None
    z_3d: Optional[float] = None
    z_7d: Optional[float] = None
    z_14d: Optional[float] = None
    max_computed_horizon: Optional[str] = None

    model_config = {"from_attributes": True}


class SimilarNewsAnalog(BaseModel):
    event_id: int
    headline: str
    event_type: str
    event_type_label: str
    first_seen_at: datetime
    similarity: float
    ret_4h_pct: Optional[float] = None
    ret_24h_pct: Optional[float] = None
    ret_3d_pct: Optional[float] = None
    ret_7d_pct: Optional[float] = None


class HorizonPrediction(BaseModel):
    horizon: str
    expected_return_pct: float
    range_low_pct: float
    range_high_pct: float
    confidence_pct: float
    directional_agreement_pct: Optional[float] = None
    analog_count: int = 0


class EventPricePrediction(BaseModel):
    symbol: str
    peak_horizon: str
    direction: str  # "bullish" | "bearish" | "neutral"
    expected_return_pct: float
    range_low_pct: float
    range_high_pct: float
    confidence_pct: float
    analog_count: int = 0
    avg_similarity: float = 0.0
    similarity_mode: str = "hybrid_tfidf_structured"
    narrative: str
    horizons: dict[str, HorizonPrediction] = {}
    analogs: list[SimilarNewsAnalog] = []


class NewsEventResponse(BaseModel):
    id: int
    headline: str
    summary: Optional[str] = None
    event_type: str
    event_type_label: str
    chokepoints: list[str] = []
    entities: list[str] = []
    primary_region: str = "global"
    first_seen_at: datetime
    market_aligned_at: datetime
    article_count: int
    distinct_sources: int
    max_source_tier: int
    novelty_score: float
    effective_direction: float
    effective_severity: float
    prior_rank_score: float
    learned_impact_score: Optional[float] = None
    llm_enriched: bool = False
    llm_confidence: Optional[float] = None
    llm_estimated_mbpd: Optional[float] = None
    llm_rationale: Optional[str] = None
    brent_impact: Optional[EventPriceReaction] = None
    wti_impact: Optional[EventPriceReaction] = None
    prediction: Optional[EventPricePrediction] = None
    articles: list[NewsArticleItem] = []


class HorizonStatCell(BaseModel):
    horizon: str
    sample_count: int = 0
    mean_ret_pct: Optional[float] = None
    median_ret_pct: Optional[float] = None
    mean_z: Optional[float] = None
    hit_rate_pct: Optional[float] = None
    t_stat: Optional[float] = None
    shrunk_weight: float = 0.0


class ImpactMatrixRow(BaseModel):
    event_type: str
    event_type_label: str
    total_events: int
    peak_horizon: Optional[str] = None
    peak_mean_ret_pct: Optional[float] = None
    peak_hit_rate_pct: Optional[float] = None
    peak_t_stat: Optional[float] = None
    persistence_label: str = "Insufficient data"
    half_life_hours: Optional[float] = None
    horizons: dict[str, HorizonStatCell] = {}


class ImpactMatrixResponse(BaseModel):
    symbol: str
    as_of_date: Optional[date] = None
    horizon_order: list[str]
    rows: list[ImpactMatrixRow]


class NewsSummaryResponse(BaseModel):
    events_24h: int
    high_impact_events_24h: int
    total_events_30d: int
    net_pressure_24h: float
    pressure_label: str
    top_event_24h: Optional[NewsEventResponse] = None
    strongest_learned_driver: Optional[ImpactMatrixRow] = None
    llm_enabled: bool
    llm_provider: Optional[str] = None
    scoring_mode: str
