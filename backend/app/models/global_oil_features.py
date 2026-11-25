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
    # News & Event Intelligence + AIS x News interaction features
    news_event_count_24h = Column(Integer)
    news_max_rank_24h = Column(Float)
    news_net_impact_1d = Column(Float)
    news_net_impact_7d = Column(Float)
    news_supply_risk_score = Column(Float)
    news_sanctions_pressure_7d = Column(Float)
    news_opec_signal_7d = Column(Float)
    news_hormuz_x_transit_drop = Column(Float)
    news_sanctions_x_dark_fleet = Column(Float)
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


class OilMacroDaily(Base):
    """Daily oil-market leading indicators: crack spreads, futures curve, macro demand proxies, tanker equities, and Gulf hurricane risk."""

    __tablename__ = "oil_macro_daily"

    date = Column(Date, primary_key=True)
    rbob_gasoline_usd = Column(Float)          # RB=F ($/gal)
    heating_oil_usd = Column(Float)            # HO=F ($/gal)
    crack_spread_321_usd = Column(Float)       # ((2*RB*42 + HO*42) / 3) - WTI ($/bbl)
    brent_wti_spread_usd = Column(Float)       # BZ=F - CL=F ($/bbl)
    dxy_close = Column(Float)                  # DX-Y.NYB (US Dollar Index)
    vix_close = Column(Float)                  # ^VIX (CBOE Volatility Index)
    tanker_etf_close = Column(Float)           # FRO (Frontline VLCC crude tanker equity)
    energy_etf_close = Column(Float)           # XLE (Energy Select Sector SPDR)
    copper_close = Column(Float)               # HG=F (Copper industrial demand proxy)
    china_etf_close = Column(Float)            # FXI (China large-cap demand proxy)
    natgas_close = Column(Float)               # NG=F (Natural gas switching proxy)
    us10y_yield = Column(Float)                # ^TNX (US 10-Year Treasury Yield)
    product_tanker_close = Column(Float)       # STNG (Scorpio Tankers product tanker proxy)
    uso_close = Column(Float)                  # USO (Front-month WTI ETF)
    usl_close = Column(Float)                  # USL (12-month strip WTI ETF)
    curve_backwardation_proxy = Column(Float)  # USO / USL ratio (rising = backwardation / tight supply)
    gulf_hurricane_risk = Column(Float)        # 0..100 NOAA NHC Gulf of Mexico storm threat score
    fetched_at = Column(TIMESTAMP(timezone=True), server_default=text("NOW()"))


class OilFundamentalsWeekly(Base):
    """Weekly physical oil storage (EIA) and hedge-fund positioning (CFTC COT Managed Money)."""

    __tablename__ = "oil_fundamentals_weekly"

    report_date = Column(Date, primary_key=True)
    cftc_mm_long = Column(Integer)
    cftc_mm_short = Column(Integer)
    cftc_mm_net = Column(Integer)
    cftc_open_interest = Column(Integer)
    cftc_mm_net_pct_oi = Column(Float)         # Managed Money Net % of Open Interest
    eia_crude_stocks_kbbl = Column(Float)      # US Commercial Crude Stocks excl. SPR (thousand barrels)
    eia_crude_stocks_chg_kbbl = Column(Float)  # Week-over-week build (+) / draw (-)
    eia_cushing_stocks_kbbl = Column(Float)    # Cushing, OK hub stocks (thousand barrels)
    eia_spr_stocks_kbbl = Column(Float)        # Strategic Petroleum Reserve (thousand barrels)
    eia_refinery_util_pct = Column(Float)      # US Refinery Utilization %
    fetched_at = Column(TIMESTAMP(timezone=True), server_default=text("NOW()"))
