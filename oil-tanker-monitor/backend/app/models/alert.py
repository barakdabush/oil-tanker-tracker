"""Alert-related database models (AIS gaps, STS events)."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class AISGap(Base):
    __tablename__ = "ais_gaps"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mmsi = Column(BigInteger, ForeignKey("vessels.mmsi"), nullable=False, index=True)
    gap_start = Column(DateTime(timezone=True), nullable=False)
    gap_end = Column(DateTime(timezone=True), nullable=True)
    last_known_lat = Column(Float, nullable=True)
    last_known_lon = Column(Float, nullable=True)
    reappear_lat = Column(Float, nullable=True)
    reappear_lon = Column(Float, nullable=True)
    distance_jumped_km = Column(Float, nullable=True)
    status = Column(
        Enum("monitoring", "dark", "extended_dark", "resolved", name="ais_gap_status"),
        nullable=False,
        default="monitoring",
    )
    spoofing_suspected = Column(Boolean, default=False)
    near_port_on_reappear = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("NOW()"))


class STSEvent(Base):
    __tablename__ = "sts_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vessel_a_mmsi = Column(BigInteger, ForeignKey("vessels.mmsi"), nullable=False)
    vessel_b_mmsi = Column(BigInteger, ForeignKey("vessels.mmsi"), nullable=False)
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=True)
    lat = Column(Float, nullable=True)
    lon = Column(Float, nullable=True)
    distance_from_port_km = Column(Float, nullable=True)
    vessel_a_draft_change = Column(Float, nullable=True)
    vessel_b_draft_change = Column(Float, nullable=True)
    estimated_volume_barrels = Column(Float, nullable=True)
    status = Column(
        Enum("detected", "ongoing", "confirmed", "dismissed", name="sts_status"),
        nullable=False,
        default="detected",
    )
    confidence = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("NOW()"))
