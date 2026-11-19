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
    cape_of_good_hope_transits = Column(Integer)
    bab_el_mandeb_transits = Column(Integer)
    suez_canal_transits = Column(Integer)
    strait_of_malacca_transits = Column(Integer)
    panama_canal_transits = Column(Integer)
    turkish_straits_transits = Column(Integer)
    danish_straits_transits = Column(Integer)
    other_straits_transits = Column(Integer)
    other_straits_traffic_share_pct = Column(Float)
    avg_chokepoint_transit_min = Column(Float)
    cape_of_good_hope_avg_transit_min = Column(Float)
    other_straits_avg_transit_min = Column(Float)
    hormuz_bab_avg_transit_min = Column(Float)
    portwatch_cape_tankers = Column(Integer)
    portwatch_other_straits_tankers = Column(Integer)
    cargo_events_24h = Column(Integer)
    estimated_volume_barrels_24h = Column(Float)
    anchored_waiting_count = Column(Integer)
    avg_port_wait_hours = Column(Float)
    port_congestion_pct = Column(Float)
    floating_storage_count = Column(Integer)
    in_transit_laden_count = Column(Integer)
    in_transit_ballast_count = Column(Integer)
    created_at = Column(TIMESTAMP(timezone=True), server_default=text("NOW()"))


class OilPrice(Base):
    """Daily oil price history fetched from EIA."""

    __tablename__ = "oil_prices"

    price_date = Column(Date, primary_key=True)
    brent_close_usd = Column(Float)
    wti_close_usd = Column(Float)
    fetched_at = Column(TIMESTAMP(timezone=True), server_default=text("NOW()"))


class StraitRiskDaily(Base):
    """Daily Hormuz & Bab el-Mandeb risk/incident signals from Straits.live and GDELT."""

    __tablename__ = "strait_risk_daily"

    date = Column(Date, primary_key=True)
    hormuz_crisis_pressure = Column(Float)
    hormuz_escalation_prob = Column(Float)
    hormuz_stranded_vessels = Column(Float)
    hormuz_incidents_count = Column(Integer)
    gdelt_strait_risk_vol = Column(Float)
    gdelt_strait_tone = Column(Float)
    fetched_at = Column(TIMESTAMP(timezone=True), server_default=text("NOW()"))
