"""Chokepoint-related database models."""

import uuid

from geoalchemy2 import Geometry
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


class Chokepoint(Base):
    __tablename__ = "chokepoints"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, unique=True)
    region = Column(String(255), nullable=True)
    boundary_polygon = Column(Geometry("POLYGON", srid=4326), nullable=True)
    avg_daily_oil_flow_mbd = Column(Float, nullable=True)
    congestion_threshold = Column(Integer, nullable=True)

    transits = relationship("ChokepointTransit", back_populates="chokepoint", lazy="dynamic")


class ChokepointTransit(Base):
    __tablename__ = "chokepoint_transits"

    time = Column(DateTime(timezone=True), primary_key=True, server_default=text("NOW()"))
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    chokepoint_id = Column(Integer, ForeignKey("chokepoints.id"), nullable=False)
    mmsi = Column(BigInteger, ForeignKey("vessels.mmsi"), nullable=False)
    entry_time = Column(DateTime(timezone=True), nullable=False)
    exit_time = Column(DateTime(timezone=True), nullable=True)
    direction = Column(String(50), nullable=True)
    transit_duration_min = Column(Float, nullable=True)
    draft_on_entry = Column(Float, nullable=True)

    chokepoint = relationship("Chokepoint", back_populates="transits")

    __table_args__ = (
        Index("idx_chokepoint_transits_chokepoint_time", "chokepoint_id", "time"),
    )
