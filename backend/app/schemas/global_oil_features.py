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

    # Chokepoint Traffic
    chokepoint_transits_24h: Optional[int] = None
    strait_of_hormuz_transits: Optional[int] = None

    # Cargo & Volume
    cargo_events_24h: Optional[int] = None
    estimated_volume_barrels_24h: Optional[float] = None

    # Oil Prices (from oil_prices table join)
    brent_close_usd: Optional[float] = None
    wti_close_usd: Optional[float] = None

    model_config = {"from_attributes": True}
