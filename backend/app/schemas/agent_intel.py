"""Pydantic schemas for the AI Agent Intelligence & Briefings API."""

from datetime import date, datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class AgentAnalyticsContext(BaseModel):
    total_vessels: int = 0
    in_transit: int = 0
    at_port: int = 0
    currently_dark: int = 0
    ongoing_sts: int = 0
    cargo_events_24h: int = 0
    daily_volume_barrels: float = 0.0
    chokepoint_transits_24h: dict[str, int] = Field(default_factory=dict)


class AgentPriceSummary(BaseModel):
    latest_date: Optional[date] = None
    brent_usd: Optional[float] = None
    wti_usd: Optional[float] = None
    brent_change_1d_pct: Optional[float] = None
    brent_change_7d_pct: Optional[float] = None
    wti_change_1d_pct: Optional[float] = None
    wti_change_7d_pct: Optional[float] = None


class AgentMarketIntelContext(BaseModel):
    prices: AgentPriceSummary = Field(default_factory=AgentPriceSummary)
    latest_snapshot_date: Optional[date] = None
    latest_snapshot: dict[str, Any] = Field(default_factory=dict)
    strait_risk_latest: dict[str, Any] = Field(default_factory=dict)
    recent_7d_averages: dict[str, Optional[float]] = Field(default_factory=dict)


class AgentNewsEventDigest(BaseModel):
    id: int
    headline: str
    event_type: str
    event_type_label: str
    chokepoints: list[str] = Field(default_factory=list)
    first_seen_at: datetime
    prior_rank_score: float
    effective_direction: float
    distinct_sources: int
    max_source_tier: int
    brent_ret_1h_pct: Optional[float] = None
    brent_ret_4h_pct: Optional[float] = None
    brent_ret_24h_pct: Optional[float] = None
    brent_ret_3d_pct: Optional[float] = None
    brent_ret_7d_pct: Optional[float] = None
    llm_rationale: Optional[str] = None


class AgentNewsIntelContext(BaseModel):
    events_24h: int = 0
    high_impact_events_24h: int = 0
    net_pressure_24h: float = 0.0
    pressure_label: str = "Balanced / Neutral"
    top_events: list[AgentNewsEventDigest] = Field(default_factory=list)
    impact_matrix_brent: list[dict[str, Any]] = Field(default_factory=list)


class AgentIntelContextResponse(BaseModel):
    """Unified, token-efficient analytics + market intel + news intel + ML forecast payload for AI agents."""

    generated_at: datetime
    window_days: int
    analytics: AgentAnalyticsContext
    market_intel: AgentMarketIntelContext
    news_intel: AgentNewsIntelContext
    forecast_ml: dict[str, Any] = Field(default_factory=dict)


class BriefingCreateRequest(BaseModel):
    window_days: int = Field(14, ge=1, le=365)
    model_used: Optional[str] = None
    market_bias: str = Field("neutral", pattern="^(bullish|bearish|neutral|mixed)$")
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    executive_summary: str = Field(..., min_length=10, max_length=4000)
    key_points: list[str] = Field(default_factory=list)
    watch_items: list[str] = Field(default_factory=list)
    full_markdown: Optional[str] = None
    context_snapshot: Optional[dict[str, Any]] = None


class BriefingResponse(BaseModel):
    id: int
    window_days: int
    model_used: Optional[str] = None
    market_bias: str
    confidence: Optional[float] = None
    executive_summary: str
    key_points: list[str] = Field(default_factory=list)
    watch_items: list[str] = Field(default_factory=list)
    full_markdown: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}
