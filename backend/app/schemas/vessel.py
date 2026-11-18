"""Vessel-related Pydantic schemas."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class VesselBase(BaseModel):
    mmsi: int
    name: Optional[str] = None
    ship_type: Optional[int] = None
    flag: Optional[str] = None
    length: Optional[float] = None
    beam: Optional[float] = None
    draft: Optional[float] = None
    dwt: Optional[float] = None
    destination: Optional[str] = None


class VesselResponse(VesselBase):
    imo: Optional[int] = None
    callsign: Optional[str] = None
    eta: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    last_lat: Optional[float] = None
    last_lon: Optional[float] = None
    last_speed: Optional[float] = None
    last_course: Optional[float] = None
    last_source: Optional[str] = None
    operational_state: Optional[str] = None
    state_since: Optional[datetime] = None
    current_port_id: Optional[int] = None
    cargo_status: Optional[str] = None
    is_sanctioned: bool = False
    sanctions_program: Optional[str] = None

    class Config:
        from_attributes = True


class VesselListResponse(BaseModel):
    vessels: list[VesselResponse]
    total: int


class PositionResponse(BaseModel):
    time: datetime
    mmsi: int
    latitude: float
    longitude: float
    speed: Optional[float] = None
    course: Optional[float] = None
    heading: Optional[float] = None
    draft: Optional[float] = None
    source: Optional[str] = None

    class Config:
        from_attributes = True


class TrailResponse(BaseModel):
    mmsi: int
    positions: list[PositionResponse]
