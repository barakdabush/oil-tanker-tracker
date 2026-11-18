"""Port database model."""

from geoalchemy2 import Geography
from sqlalchemy import (
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
from sqlalchemy.orm import relationship

from app.database import Base


class Port(Base):
    __tablename__ = "ports"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False, index=True)
    country = Column(String(100), nullable=True)
    region = Column(String(100), nullable=True)
    port_type = Column(String(50), nullable=True)  # oil_terminal, port, refinery
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    position = Column(Geography("POINT", srid=4326, spatial_index=False), nullable=True)
    max_vessel_size = Column(String(50), nullable=True)
    oil_capacity_barrels = Column(Float, nullable=True)

    cargo_events = relationship("CargoEvent", back_populates="port", lazy="dynamic")

    __table_args__ = (
        Index("idx_ports_position", "position", postgresql_using="gist"),
    )


class PortDailyStat(Base):
    """Daily port fullness and anchorage congestion rollup."""

    __tablename__ = "port_daily_stats"

    port_id = Column(Integer, ForeignKey("ports.id", ondelete="CASCADE"), primary_key=True)
    stat_date = Column(Date, primary_key=True)
    berthed_count = Column(Integer, nullable=False, default=0)
    anchored_waiting_count = Column(Integer, nullable=False, default=0)
    avg_wait_hours = Column(Float, nullable=True)
    utilization_pct = Column(Float, nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=text("NOW()"))
