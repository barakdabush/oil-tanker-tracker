"""Market analytics Pydantic schemas."""

from datetime import date
from typing import Optional

from pydantic import BaseModel


class MarketSnapshotResponse(BaseModel):
    """Combined market snapshot + oil price data for ML analysis."""

    snapshot_date: date

    # Fleet Activity
    total_active_vessels: Optional[int] = None
    vessels_in_transit: Optional[int] = None
    vessels_at_port: Optional[int] = None
    avg_fleet_speed: Optional[float] = None
    vessels_idle_gt_48h: Optional[int] = None

    # Dark Fleet / Sanctions Evasion
    dark_vessels_count: Optional[int] = None
    new_ais_gaps_24h: Optional[int] = None
    resolved_gaps_24h: Optional[int] = None
    avg_gap_duration_hours: Optional[float] = None

    # STS Transfers
    sts_events_24h: Optional[int] = None
    sts_confirmed_24h: Optional[int] = None

    # Chokepoint Traffic & Delays
    chokepoint_transits_24h: Optional[int] = None
    strait_of_hormuz_transits: Optional[int] = None
    cape_of_good_hope_transits: Optional[int] = None
    bab_el_mandeb_transits: Optional[int] = None
    suez_canal_transits: Optional[int] = None
    strait_of_malacca_transits: Optional[int] = None
    panama_canal_transits: Optional[int] = None
    turkish_straits_transits: Optional[int] = None
    danish_straits_transits: Optional[int] = None
    other_straits_transits: Optional[int] = None
    other_straits_traffic_share_pct: Optional[float] = None
    avg_chokepoint_transit_min: Optional[float] = None
    cape_of_good_hope_avg_transit_min: Optional[float] = None
    other_straits_avg_transit_min: Optional[float] = None
    hormuz_bab_avg_transit_min: Optional[float] = None
    portwatch_cape_tankers: Optional[int] = None
    portwatch_other_straits_tankers: Optional[int] = None
    portwatch_hormuz_tankers: Optional[int] = None
    portwatch_hormuz_capacity_tanker: Optional[float] = None
    portwatch_bab_el_mandeb_tankers: Optional[int] = None
    portwatch_suez_tankers: Optional[int] = None

    # Strait Risk & Geopolitical Signals (Straits.live + GDELT)
    hormuz_crisis_pressure: Optional[float] = None
    hormuz_escalation_prob: Optional[float] = None
    hormuz_stranded_vessels: Optional[float] = None
    hormuz_incidents_count: Optional[int] = None
    gdelt_strait_risk_vol: Optional[float] = None
    gdelt_strait_tone: Optional[float] = None

    # Cargo & Volume
    cargo_events_24h: Optional[int] = None
    estimated_volume_barrels_24h: Optional[float] = None

    # Operational States & Port Congestion
    anchored_waiting_count: Optional[int] = None
    avg_port_wait_hours: Optional[float] = None
    port_congestion_pct: Optional[float] = None
    floating_storage_count: Optional[int] = None
    in_transit_laden_count: Optional[int] = None
    in_transit_ballast_count: Optional[int] = None

    # Oil Prices (from oil_prices table join)
    brent_close_usd: Optional[float] = None
    wti_close_usd: Optional[float] = None

    model_config = {"from_attributes": True}
