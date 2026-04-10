"""Chokepoint-related Pydantic schemas."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class ChokepointResponse(BaseModel):
    id: int
    name: str
    region: Optional[str] = None
    avg_daily_oil_flow_mbd: Optional[float] = None
    congestion_threshold: Optional[int] = None
    current_vessel_count: Optional[int] = 0
    congestion_status: Optional[str] = "normal"
    boundary_geojson: Optional[dict] = None

    class Config:
        from_attributes = True


class ChokepointTransitResponse(BaseModel):
    id: UUID
    chokepoint_id: int
    mmsi: int
    entry_time: datetime
    exit_time: Optional[datetime] = None
    direction: Optional[str] = None
    transit_duration_min: Optional[float] = None
    draft_on_entry: Optional[float] = None

    class Config:
        from_attributes = True
