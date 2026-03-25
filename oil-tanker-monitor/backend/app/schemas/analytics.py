"""Analytics-related Pydantic schemas."""

from pydantic import BaseModel

from app.schemas.cargo import CargoEventResponse


class FlowDataPoint(BaseModel):
    source_region: str
    destination_region: str
    volume_barrels: float
    vessel_count: int


class VolumeOverTime(BaseModel):
    date: str
    volume_barrels: float
    event_count: int


class TopRoute(BaseModel):
    origin_port: str
    destination_port: str
    voyage_count: int
    total_volume_barrels: float


class FleetStatus(BaseModel):
    total_vessels: int
    in_transit: int
    at_port: int
    currently_dark: int
    loaded: int
    ballast: int


class DashboardStats(BaseModel):
    fleet: FleetStatus
    active_alerts: int
    ongoing_sts_events: int
    congested_chokepoints: int
    recent_cargo_events: list[CargoEventResponse]
    daily_volume_barrels: float


class IngestionDataPoint(BaseModel):
    """A single time-bucket data point for ingestion analytics."""
    timestamp: str
    vessel_positions: int = 0
    chokepoint_transits: int = 0
    sts_events: int = 0
    ais_gaps: int = 0
    cargo_events: int = 0
