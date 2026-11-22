"""Pydantic schemas for satellite SAR vessel detections and scenes."""

from datetime import datetime
from typing import Optional
from uuid import UUID

from pydantic import BaseModel


class SatelliteDetectionResponse(BaseModel):
    id: UUID
    source: str
    scene_id: Optional[str] = None
    detected_at: datetime
    latitude: float
    longitude: float
    chokepoint_name: Optional[str] = None
    estimated_length_m: Optional[float] = None
    radar_strength_db: Optional[float] = None
    confidence: Optional[float] = None
    is_dark: bool
    matched_mmsi: Optional[int] = None
    matched_vessel_name: Optional[str] = None
    match_distance_km: Optional[float] = None
    detection_count: int = 1

    class Config:
        from_attributes = True


class SatelliteDetectionListResponse(BaseModel):
    detections: list[SatelliteDetectionResponse]
    total: int
    dark_total: int
    matched_total: int


class SatelliteSceneResponse(BaseModel):
    scene_id: str
    satellite: str
    chokepoint_name: str
    acquired_at: datetime
    status: str
    detections_count: int
    dark_detections_count: int
    processed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class SatelliteSummaryResponse(BaseModel):
    total_detections: int
    dark_detections: int
    matched_detections: int
    by_source: dict[str, int]
    latest_scene: Optional[SatelliteSceneResponse] = None
    sources_configured: dict[str, bool]
