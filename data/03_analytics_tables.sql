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
