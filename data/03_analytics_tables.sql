-- ============================================================
-- Analytics tables for ML feature pipeline
-- ============================================================

-- Daily aggregated features for ML
CREATE TABLE IF NOT EXISTS oil_market_snapshots (
    snapshot_date DATE PRIMARY KEY,
    total_active_vessels INT,
    vessels_in_transit INT,
    vessels_at_port INT,
    avg_fleet_speed FLOAT,
    vessels_idle_gt_48h INT,
    dark_vessels_count INT,
    new_ais_gaps_24h INT,
    resolved_gaps_24h INT,
    avg_gap_duration_hours FLOAT,
    sts_events_24h INT,
    sts_confirmed_24h INT,
    chokepoint_transits_24h INT,
    strait_of_hormuz_transits INT,
    cape_of_good_hope_transits INT,
    bab_el_mandeb_transits INT,
    suez_canal_transits INT,
    strait_of_malacca_transits INT,
    panama_canal_transits INT,
    turkish_straits_transits INT,
    danish_straits_transits INT,
    other_straits_transits INT,
    other_straits_traffic_share_pct FLOAT,
    avg_chokepoint_transit_min FLOAT,
    cape_of_good_hope_avg_transit_min FLOAT,
    other_straits_avg_transit_min FLOAT,
    hormuz_bab_avg_transit_min FLOAT,
    portwatch_cape_tankers INT,
    portwatch_other_straits_tankers INT,
    cargo_events_24h INT,
    estimated_volume_barrels_24h FLOAT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Oil price history from EIA
CREATE TABLE IF NOT EXISTS oil_prices (
    price_date DATE PRIMARY KEY,
    brent_close_usd FLOAT,
    wti_close_usd FLOAT,
    fetched_at TIMESTAMPTZ DEFAULT NOW()
);

-- External Hormuz & Bab el-Mandeb risk/incident signals (Straits.live + GDELT)
CREATE TABLE IF NOT EXISTS strait_risk_daily (
    date DATE PRIMARY KEY,
    hormuz_crisis_pressure FLOAT,
    hormuz_escalation_prob FLOAT,
    hormuz_stranded_vessels FLOAT,
    hormuz_incidents_count INT,
    gdelt_strait_risk_vol FLOAT,
    gdelt_strait_tone FLOAT,
    fetched_at TIMESTAMPTZ DEFAULT NOW()
);
