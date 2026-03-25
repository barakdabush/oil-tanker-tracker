"""Port-related Pydantic schemas."""

from typing import Optional

from pydantic import BaseModel


class PortResponse(BaseModel):
    id: int
    name: str
    country: Optional[str] = None
    region: Optional[str] = None
    port_type: Optional[str] = None
    latitude: float
    longitude: float
    oil_capacity_barrels: Optional[float] = None

    class Config:
        from_attributes = True


class PortListResponse(BaseModel):
    ports: list[PortResponse]
    total: int
