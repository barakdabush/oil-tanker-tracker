"""Market analytics database models."""

from sqlalchemy import Column, Date, Float, Integer, text
from sqlalchemy.dialects.postgresql import TIMESTAMP

from app.database import Base


class OilMarketSnapshot(Base):
    """Daily aggregated fleet and market features for ML analysis."""

    __tablename__ = "oil_market_snapshots"

    snapshot_date = Column(Date, primary_key=True)
    total_active_vessels = Column(Integer)
    vessels_in_transit = Column(Integer)
    vessels_at_port = Column(Integer)
    avg_fleet_speed = Column(Float)
    vessels_idle_gt_48h = Column(Integer)
    dark_vessels_count = Column(Integer)
    new_ais_gaps_24h = Column(Integer)
    resolved_gaps_24h = Column(Integer)
    avg_gap_duration_hours = Column(Float)
    sts_events_24h = Column(Integer)
    sts_confirmed_24h = Column(Integer)
    chokepoint_transits_24h = Column(Integer)
    strait_of_hormuz_transits = Column(Integer)
    cargo_events_24h = Column(Integer)
    estimated_volume_barrels_24h = Column(Float)
    created_at = Column(TIMESTAMP(timezone=True), server_default=text("NOW()"))


class OilPrice(Base):
    """Daily oil price history fetched from EIA."""

    __tablename__ = "oil_prices"

    price_date = Column(Date, primary_key=True)
    brent_close_usd = Column(Float)
    wti_close_usd = Column(Float)
    fetched_at = Column(TIMESTAMP(timezone=True), server_default=text("NOW()"))
