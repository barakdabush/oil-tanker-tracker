"""Satellite SAR vessel detection & overpass API endpoints."""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.satellite import SatelliteDetection, SatelliteScene
from app.schemas.satellite import (
    SatelliteDetectionListResponse,
    SatelliteDetectionResponse,
    SatelliteSceneResponse,
    SatelliteSummaryResponse,
)

router = APIRouter()


@router.get("/detections", response_model=SatelliteDetectionListResponse)
async def list_satellite_detections(
    hours: int = Query(336, ge=1, le=2160, description="Lookback window in hours (default 14 days)"),
    source: Optional[str] = Query(None, description="Filter by source: copernicus_s1, gfw_sar"),
    chokepoint: Optional[str] = Query(None, description="Filter by chokepoint name"),
    dark_only: bool = Query(False, description="Only return unmatched (dark) vessels"),
    lat_min: Optional[float] = Query(None),
    lon_min: Optional[float] = Query(None),
    lat_max: Optional[float] = Query(None),
    lon_max: Optional[float] = Query(None),
    limit: int = Query(500, ge=1, le=2000),
    db: AsyncSession = Depends(get_db),
):
    """List satellite SAR vessel detections (Sentinel-1 & Global Fishing Watch)."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    query = select(SatelliteDetection).where(SatelliteDetection.detected_at >= cutoff)

    if source:
        query = query.where(SatelliteDetection.source == source)
    if chokepoint:
        query = query.where(SatelliteDetection.chokepoint_name == chokepoint)
    if dark_only:
        query = query.where(SatelliteDetection.is_dark.is_(True))
    if None not in (lat_min, lon_min, lat_max, lon_max):
        query = query.where(
            SatelliteDetection.latitude.between(lat_min, lat_max),
            SatelliteDetection.longitude.between(lon_min, lon_max),
        )

    base_sub = query.subquery()
    total = (await db.execute(select(func.count()).select_from(base_sub))).scalar() or 0
    dark_total = (
        await db.execute(
            select(func.count()).select_from(base_sub).where(base_sub.c.is_dark.is_(True))
        )
    ).scalar() or 0

    rows = (
        await db.execute(
            query.order_by(desc(SatelliteDetection.detected_at)).limit(limit)
        )
    ).scalars().all()

    return SatelliteDetectionListResponse(
        detections=[SatelliteDetectionResponse.model_validate(r) for r in rows],
        total=total,
        dark_total=dark_total,
        matched_total=max(total - dark_total, 0),
    )


@router.get("/scenes", response_model=list[SatelliteSceneResponse])
async def list_satellite_scenes(
    chokepoint: Optional[str] = Query("Strait of Hormuz"),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """List recent Copernicus Sentinel-1 satellite overpasses."""
    query = select(SatelliteScene)
    if chokepoint:
        query = query.where(SatelliteScene.chokepoint_name == chokepoint)
    rows = (
        await db.execute(query.order_by(desc(SatelliteScene.acquired_at)).limit(limit))
    ).scalars().all()
    return [SatelliteSceneResponse.model_validate(r) for r in rows]


@router.get("/summary", response_model=SatelliteSummaryResponse)
async def get_satellite_summary(
    hours: int = Query(336, ge=1, le=2160),
    chokepoint: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
):
    """Summary of satellite SAR detections, latest Sentinel-1 pass, and configured sources."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    conds = [SatelliteDetection.detected_at >= cutoff]
    if chokepoint:
        conds.append(SatelliteDetection.chokepoint_name == chokepoint)

    source_rows = (
        await db.execute(
            select(SatelliteDetection.source, func.count())
            .where(*conds)
            .group_by(SatelliteDetection.source)
        )
    ).all()
    by_source = {src: int(cnt) for src, cnt in source_rows}
    total = sum(by_source.values())

    dark = (
        await db.execute(
            select(func.count())
            .select_from(SatelliteDetection)
            .where(*conds, SatelliteDetection.is_dark.is_(True))
        )
    ).scalar() or 0

    latest_scene_row = (
        await db.execute(
            select(SatelliteScene).order_by(desc(SatelliteScene.acquired_at)).limit(1)
        )
    ).scalar_one_or_none()

    return SatelliteSummaryResponse(
        total_detections=total,
        dark_detections=int(dark),
        matched_detections=max(total - int(dark), 0),
        by_source=by_source,
        latest_scene=(
            SatelliteSceneResponse.model_validate(latest_scene_row) if latest_scene_row else None
        ),
        sources_configured={
            "hormuz_now": bool(settings.HORMUZ_NOW_ENABLED),
            "copernicus_s1_catalog": bool(settings.SENTINEL1_ENABLED),
            "copernicus_s1_process": bool(
                settings.SENTINEL1_ENABLED and settings.CDSE_CLIENT_ID and settings.CDSE_CLIENT_SECRET
            ),
            "gfw_sar": bool(settings.GFW_SAR_ENABLED and settings.GFW_API_TOKEN),
        },
    )
