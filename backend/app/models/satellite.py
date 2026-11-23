"""Satellite SAR scene and vessel detection models."""

import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class SatelliteScene(Base):
    """Cataloged and processed satellite SAR overpasses (e.g. Sentinel-1 over Hormuz)."""

    __tablename__ = "satellite_scenes"

    scene_id = Column(String(128), primary_key=True)
    satellite = Column(String(16), nullable=False)  # S1A, S1C, S1D
    chokepoint_name = Column(String(100), nullable=False, index=True)
    acquired_at = Column(DateTime(timezone=True), nullable=False, index=True)
    footprint_geojson = Column(Text, nullable=True)
    status = Column(String(32), nullable=False, default="cataloged")
    # cataloged | processed | skipped_no_creds | error
    detections_count = Column(Integer, nullable=False, default=0)
    dark_detections_count = Column(Integer, nullable=False, default=0)
    error_message = Column(Text, nullable=True)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("NOW()"))


class SatelliteDetection(Base):
    """Individual vessel position detected from open-source satellite SAR imagery."""

    __tablename__ = "satellite_detections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source = Column(String(32), nullable=False, index=True)  # copernicus_s1 | gfw_sar
    scene_id = Column(String(128), nullable=True, index=True)
    detected_at = Column(DateTime(timezone=True), nullable=False, index=True)
    latitude = Column(Float, nullable=False)
    longitude = Column(Float, nullable=False)
    chokepoint_name = Column(String(100), nullable=True, index=True)
    estimated_length_m = Column(Float, nullable=True)
    radar_strength_db = Column(Float, nullable=True)
    confidence = Column(Float, nullable=True)
    is_dark = Column(Boolean, nullable=False, default=True, index=True)
    matched_mmsi = Column(
        BigInteger,
        ForeignKey("vessels.mmsi", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    matched_vessel_name = Column(String(255), nullable=True)
    match_distance_km = Column(Float, nullable=True)
    detection_count = Column(Integer, nullable=False, default=1)
    dedup_key = Column(String(128), nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), server_default=text("NOW()"))

    __table_args__ = (
        Index("idx_sat_det_time_dark", "detected_at", "is_dark"),
        Index("idx_sat_det_lat_lon", "latitude", "longitude"),
    )
