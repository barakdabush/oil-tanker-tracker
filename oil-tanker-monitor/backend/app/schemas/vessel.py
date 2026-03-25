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

    class Config:
        from_attributes = True


class TrailResponse(BaseModel):
    mmsi: int
    positions: list[PositionResponse]
