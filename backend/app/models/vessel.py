"""Vessel-related database models."""

from datetime import datetime
import uuid

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from app.database import Base


class Vessel(Base):
    __tablename__ = "vessels"

    mmsi = Column(BigInteger, primary_key=True)
    imo = Column(BigInteger, nullable=True, index=True)
    name = Column(String(255), nullable=True, index=True)
    ship_type = Column(Integer, nullable=True)  # AIS type 80-89 for tankers
    flag = Column(String(10), nullable=True)
    callsign = Column(String(20), nullable=True)
    length = Column(Float, nullable=True)
    beam = Column(Float, nullable=True)
    draft = Column(Float, nullable=True)  # Current draft
    dwt = Column(Float, nullable=True)  # Deadweight tonnage
    destination = Column(String(255), nullable=True)
    eta = Column(DateTime(timezone=True), nullable=True)
    last_seen = Column(DateTime(timezone=True), nullable=True)
    last_lat = Column(Float, nullable=True)
    last_lon = Column(Float, nullable=True)
    last_speed = Column(Float, nullable=True)
    last_course = Column(Float, nullable=True)
    last_source = Column(String(64), nullable=True)
    operational_state = Column(String(32), nullable=True, index=True)
    state_since = Column(DateTime(timezone=True), nullable=True)
    current_port_id = Column(Integer, ForeignKey("ports.id", ondelete="SET NULL"), nullable=True, index=True)
    cargo_status = Column(String(16), nullable=True)  # laden, ballast, unknown
    min_draft = Column(Float, nullable=True)
    max_draft = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("NOW()"))
    updated_at = Column(DateTime(timezone=True), server_default=text("NOW()"), onupdate=datetime.utcnow)

    positions = relationship("VesselPosition", back_populates="vessel", lazy="dynamic")
    cargo_events = relationship("CargoEvent", back_populates="vessel", lazy="dynamic")
    state_history = relationship("VesselStateHistory", back_populates="vessel", lazy="dynamic")


class VesselStateHistory(Base):
    """Interval-based log of vessel operational state transitions."""

    __tablename__ = "vessel_state_history"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    mmsi = Column(BigInteger, ForeignKey("vessels.mmsi", ondelete="CASCADE"), nullable=False, index=True)
    state = Column(String(32), nullable=False, index=True)
    cargo_status = Column(String(16), nullable=True)
    port_id = Column(Integer, ForeignKey("ports.id", ondelete="SET NULL"), nullable=True, index=True)
    started_at = Column(DateTime(timezone=True), nullable=False)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    duration_hours = Column(Float, nullable=True)
    start_lat = Column(Float, nullable=True)
    start_lon = Column(Float, nullable=True)

    vessel = relationship("Vessel", back_populates="state_history")

    __table_args__ = (
        Index("idx_vsh_mmsi_started", "mmsi", "started_at"),
        Index("idx_vsh_state_started", "state", "started_at"),
        Index("idx_vsh_port_started", "port_id", "started_at"),
    )


class VesselPosition(Base):
    __tablename__ = "vessel_positions"

    time = Column(DateTime(timezone=True), primary_key=True, server_default=text("NOW()"))
    mmsi = Column(BigInteger, ForeignKey("vessels.mmsi"), primary_key=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    speed = Column(Float, nullable=True)  # Speed over ground (knots)
    course = Column(Float, nullable=True)  # Course over ground (degrees)
    heading = Column(Float, nullable=True)  # True heading (degrees)
    draft = Column(Float, nullable=True)
    nav_status = Column(Integer, nullable=True)  # Navigation status code
    source = Column(String(64), nullable=True)

    vessel = relationship("Vessel", back_populates="positions")

    __table_args__ = (
        Index("idx_vessel_positions_mmsi_time", "mmsi", "time"),
    )
