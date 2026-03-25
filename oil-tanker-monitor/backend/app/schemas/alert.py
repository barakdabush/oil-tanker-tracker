"""Alert-related Pydantic schemas (AIS gaps, STS events)."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class AISGapResponse(BaseModel):
    id: UUID
    mmsi: int
    gap_start: datetime
    gap_end: Optional[datetime] = None
    last_known_lat: Optional[float] = None
    last_known_lon: Optional[float] = None
    reappear_lat: Optional[float] = None
    reappear_lon: Optional[float] = None
    distance_jumped_km: Optional[float] = None
    status: str
    spoofing_suspected: bool = False
    near_port_on_reappear: Optional[str] = None

    class Config:
        from_attributes = True


class STSEventResponse(BaseModel):
    id: UUID
    vessel_a_mmsi: int
    vessel_b_mmsi: int
    start_time: datetime
    end_time: Optional[datetime] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    distance_from_port_km: Optional[float] = None
    vessel_a_draft_change: Optional[float] = None
    vessel_b_draft_change: Optional[float] = None
    estimated_volume_barrels: Optional[float] = None
    status: str
    confidence: Optional[float] = None

    class Config:
        from_attributes = True
