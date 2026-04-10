"""Cargo-related database models."""

import uuid

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class CargoEvent(Base):
    __tablename__ = "cargo_events"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mmsi = Column(BigInteger, ForeignKey("vessels.mmsi", ondelete="CASCADE"), nullable=False, index=True)
    port_id = Column(Integer, ForeignKey("ports.id"), nullable=True)
    event_type = Column(Enum("loading", "unloading", "unknown", name="cargo_event_type"), nullable=False)
    arrival_time = Column(DateTime(timezone=True), nullable=True)
    departure_time = Column(DateTime(timezone=True), nullable=True)
    draft_arrival = Column(Float, nullable=True)
    draft_departure = Column(Float, nullable=True)
    draft_change = Column(Float, nullable=True)
    estimated_volume_barrels = Column(Float, nullable=True)
    confidence = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("NOW()"))

    vessel = relationship("Vessel", back_populates="cargo_events")
    port = relationship("Port", back_populates="cargo_events")


class VoyageLeg(Base):
    __tablename__ = "voyage_legs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mmsi = Column(BigInteger, ForeignKey("vessels.mmsi", ondelete="CASCADE"), nullable=False, index=True)
    origin_port_id = Column(Integer, ForeignKey("ports.id"), nullable=True)
    destination_port_id = Column(Integer, ForeignKey("ports.id"), nullable=True)
    departure_time = Column(DateTime(timezone=True), nullable=True)
    arrival_time = Column(DateTime(timezone=True), nullable=True)
    cargo_type = Column(Enum("loaded", "ballast", "unknown", name="cargo_type"), nullable=True)
    estimated_volume_barrels = Column(Float, nullable=True)
    distance_nm = Column(Float, nullable=True)
