"""Pydantic schemas for request/response models.

All schemas are re-exported here so existing imports like
``from app.schemas import VesselResponse`` continue to work.
"""

from app.schemas.vessel import (  # noqa: F401
    VesselBase,
    VesselResponse,
    VesselListResponse,
    PositionResponse,
    TrailResponse,
)
from app.schemas.port import PortResponse, PortListResponse  # noqa: F401
from app.schemas.cargo import CargoEventResponse  # noqa: F401
from app.schemas.alert import AISGapResponse, STSEventResponse  # noqa: F401
from app.schemas.chokepoint import (  # noqa: F401
    ChokepointResponse,
    ChokepointTransitResponse,
)
from app.schemas.analytics import (  # noqa: F401
    FlowDataPoint,
    VolumeOverTime,
    TopRoute,
    FleetStatus,
    DashboardStats,
    IngestionDataPoint,
)

__all__ = [
    "VesselBase",
    "VesselResponse",
    "VesselListResponse",
    "PositionResponse",
    "TrailResponse",
    "PortResponse",
    "PortListResponse",
    "CargoEventResponse",
    "AISGapResponse",
    "STSEventResponse",
    "ChokepointResponse",
    "ChokepointTransitResponse",
    "FlowDataPoint",
    "VolumeOverTime",
    "TopRoute",
    "FleetStatus",
    "DashboardStats",
    "IngestionDataPoint",
]
