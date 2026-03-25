"""SQLAlchemy models for TimescaleDB with PostGIS support.

All models are re-exported here so existing imports like
``from app.models import Vessel`` continue to work.
"""

from app.models.vessel import Vessel, VesselPosition  # noqa: F401
from app.models.port import Port  # noqa: F401
from app.models.cargo import CargoEvent, VoyageLeg  # noqa: F401
from app.models.alert import AISGap, STSEvent  # noqa: F401
from app.models.chokepoint import Chokepoint, ChokepointTransit  # noqa: F401

__all__ = [
    "Vessel",
    "VesselPosition",
    "Port",
    "CargoEvent",
    "VoyageLeg",
    "AISGap",
    "STSEvent",
    "Chokepoint",
    "ChokepointTransit",
]
