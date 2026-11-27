"""Pydantic schemas for the Unified Bottom-Line Oil Price Predictor API."""

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel


class ForecastPillar(BaseModel):
    key: str
    label: str
    score: float  # -100 (strongly bearish) .. +100 (strongly bullish)
    weight: float  # 0..1
    direction: str  # "bullish" | "bearish" | "neutral"
    summary: str
    metrics: list[str] = []


class ForecastDriver(BaseModel):
    source_type: str  # "news" | "chokepoint" | "dark_fleet" | "market"
    title: str
    impact_direction: str  # "bullish" | "bearish" | "neutral"
    expected_move_pct: Optional[float] = None
    horizon: Optional[str] = None
    confidence_pct: Optional[float] = None
    detail: str


class VolatilityAlertSignal(BaseModel):
    threshold_pct: Optional[float] = None
    live_prob_pct: Optional[float] = None
    alert_state: str = "Moderate"  # "High Volatility Alert" | "Moderate" | "Calm Regime"
    indep_accuracy_pct: Optional[float] = None
    indep_samples: int = 0
    hc_accuracy_pct: Optional[float] = None
    hc_samples: int = 0
    tier1_accuracy_pct: Optional[float] = None
    tier1_samples: int = 0
    tier1_p_value: Optional[float] = None
    roc_auc: Optional[float] = None
    explanation: str = ""


class SupplyShockSpikeSignal(BaseModel):
    threshold_pct: Optional[float] = None
    live_spike_prob_pct: Optional[float] = None
    alert_state: str = "Normal"  # "Spike Risk Elevated" | "Normal"
    base_rate_pct: Optional[float] = None
    alert_precision_pct: Optional[float] = None
    alert_samples: int = 0
    lift_x: Optional[float] = None
    indep_p_value: Optional[float] = None
    roc_auc: Optional[float] = None
    explanation: str = ""


class HorizonForecast(BaseModel):
    horizon: str  # "3d" | "5d" | "7d"
    verdict: str  # "Strong Bullish" | "Bullish" | "Neutral" | "Bearish" | "Strong Bearish"
    direction: str  # "bullish" | "bearish" | "neutral"
    predicted_return_pct: float
    predicted_price_usd: Optional[float] = None
    price_low_usd: Optional[float] = None
    price_high_usd: Optional[float] = None
    confidence_pct: float
    pillar_return_pct: float
    ml_return_pct: Optional[float] = None
    ml_weight: float = 0.0
    backtest_hit_rate_pct: Optional[float] = None
    backtest_mae_pct: Optional[float] = None
    backtest_samples: int = 0
    high_conviction_hit_rate_pct: Optional[float] = None
    high_conviction_samples: int = 0
    indep_hit_rate_pct: Optional[float] = None
    indep_samples: int = 0
    indep_p_value: Optional[float] = None
    up_wave_prob_pct: Optional[float] = None
    down_wave_prob_pct: Optional[float] = None
    range_prob_pct: Optional[float] = None
    wave_state: str = "Range-Bound"  # "Bullish Wave" | "Bearish Wave" | "Range-Bound"
    model_name: str = "4-Pillar + HAR-OVX-ML"
    volatility_alert: Optional[VolatilityAlertSignal] = None
    spike_radar: Optional[SupplyShockSpikeSignal] = None


class BottomLineForecastResponse(BaseModel):
    symbol: str
    symbol_label: str
    as_of: datetime
    current_price_usd: Optional[float] = None
    price_date: Optional[date] = None
    daily_vol_pct: float
    composite_score: float  # -100 .. +100
    primary_horizon: str = "3d"
    verdict: str
    direction: str
    headline_summary: str
    horizons: dict[str, HorizonForecast]
    pillars: list[ForecastPillar]
    top_drivers: list[ForecastDriver]
    active_news_count_48h: int = 0
    analog_memory_size: int = 0
    latest_briefing_bias: Optional[str] = None
    latest_briefing_summary: Optional[str] = None
