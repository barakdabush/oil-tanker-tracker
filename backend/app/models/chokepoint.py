"""Chokepoint-related database models."""

import uuid

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Column,
    Date,
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


class PortWatchChokepointDaily(Base):
    __tablename__ = "portwatch_chokepoint_daily"

    portid = Column(String(32), primary_key=True)
    date = Column(Date, primary_key=True)
    portname = Column(String(128), nullable=False, index=True)
    n_total = Column(Integer, nullable=False, default=0)
    n_tanker = Column(Integer, nullable=False, default=0)
    n_cargo = Column(Integer, nullable=False, default=0)
    n_container = Column(Integer, nullable=False, default=0)
    capacity_tanker = Column(Float, nullable=True)
    capacity_total = Column(Float, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=text("NOW()"))

    __table_args__ = (
        Index("idx_portwatch_portname_date", "portname", "date"),
    )
