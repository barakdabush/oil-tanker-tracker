"""SQLAlchemy models for TimescaleDB with PostGIS support.

All models are re-exported here so existing imports like
``from app.models import Vessel`` continue to work.
"""

from app.models.vessel import Vessel, VesselPosition, VesselStateHistory  # noqa: F401
from app.models.port import Port, PortDailyStat  # noqa: F401
from app.models.cargo import CargoEvent, VoyageLeg  # noqa: F401
from app.models.alert import AISGap, STSEvent  # noqa: F401
from app.models.chokepoint import Chokepoint, ChokepointTransit, PortWatchChokepointDaily  # noqa: F401
from app.models.global_oil_features import OilMarketSnapshot, OilPrice, StraitRiskDaily  # noqa: F401
from app.models.sanctions import OfacSanctionedVessel  # noqa: F401
from app.models.system_log import SystemLog  # noqa: F401
from app.models.incident import Incident  # noqa: F401
from app.models.news import (  # noqa: F401
    OilPriceIntraday,
    NewsEvent,
    NewsArticle,
    NewsEventPriceImpact,
    NewsImpactPrior,
)

__all__ = [
    "Vessel",
    "VesselPosition",
    "VesselStateHistory",
    "Port",
    "PortDailyStat",
    "CargoEvent",
    "VoyageLeg",
    "AISGap",
    "STSEvent",
    "Chokepoint",
    "ChokepointTransit",
    "PortWatchChokepointDaily",
    "OilMarketSnapshot",
    "OilPrice",
    "StraitRiskDaily",
    "OfacSanctionedVessel",
    "SystemLog",
    "Incident",
    "OilPriceIntraday",
    "NewsEvent",
    "NewsArticle",
    "NewsEventPriceImpact",
    "NewsImpactPrior",
]
