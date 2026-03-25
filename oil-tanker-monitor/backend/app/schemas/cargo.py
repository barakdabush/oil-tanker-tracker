"""Cargo-related Pydantic schemas."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class CargoEventResponse(BaseModel):
    id: UUID
    mmsi: int
    port_id: Optional[int] = None
    port_name: Optional[str] = None
    event_type: str
    arrival_time: Optional[datetime] = None
    departure_time: Optional[datetime] = None
    draft_arrival: Optional[float] = None
    draft_departure: Optional[float] = None
    draft_change: Optional[float] = None
    estimated_volume_barrels: Optional[float] = None
    confidence: Optional[float] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True
