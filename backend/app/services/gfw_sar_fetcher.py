"""Global Fishing Watch (GFW) v3 SAR Vessel Detections fetcher.

Uses the free Global Fishing Watch 4Wings Report API (``public-global-sar-presence:latest``)
derived from ESA Copernicus Sentinel-1 SAR imagery to ingest both:
- Unmatched ("dark") satellite vessel detections (``matched='false'``)
- AIS-matched satellite vessel detections (``matched='true'``)

Requires a free API token (``GFW_API_TOKEN``) from https://globalfishingwatch.org/our-apis/
"""

import asyncio
import hashlib
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings
from app.database import async_session
from app.models.satellite import SatelliteDetection

logger = logging.getLogger(__name__)

GFW_REPORT_URL = "https://gateway.api.globalfishingwatch.org/v3/4wings/report"
GFW_SAR_DATASET = "public-global-sar-presence:latest"

# Chokepoints prioritized for GFW SAR dark-vessel & presence monitoring
# Format: (name, (min_lon, min_lat, max_lon, max_lat))
GFW_MONITORED_REGIONS: list[tuple[str, tuple[float, float, float, float]]] = [
    ("Strait of Hormuz", (54.5, 24.5, 58.5, 27.5)),
    ("Bab el-Mandeb", (42.0, 11.5, 45.0, 14.5)),
    ("Suez Canal", (31.8, 28.5, 34.2, 31.8)),
    ("Strait of Malacca", (99.5, 1.0, 104.5, 5.5)),
]


def _bbox_to_geojson_polygon(bbox: tuple[float, float, float, float]) -> dict:
    min_lon, min_lat, max_lon, max_lat = bbox
    return {
        "type": "Polygon",
        "coordinates": [[
            [min_lon, min_lat],
            [max_lon, min_lat],
            [max_lon, max_lat],
            [min_lon, max_lat],
            [min_lon, min_lat],
        ]],
    }


def _parse_gfw_date(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    s = str(raw).strip()
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _make_gfw_dedup_key(chokepoint: str, detected_at: datetime, lat: float, lon: float, is_dark: bool) -> str:
    raw = f"gfw_sar:{chokepoint}:{detected_at.isoformat()}:{lat:.3f}:{lon:.3f}:{int(is_dark)}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:40]


def parse_gfw_sar_response(payload: dict, chokepoint_name: str, is_dark: bool) -> list[dict]:
    """Parse a GFW v3 4Wings JSON report into SatelliteDetection insert dicts."""
    entries = payload.get("entries")
    if not isinstance(entries, list):
        return []

    rows: list[dict] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        for dataset_key, points in entry.items():
            if not isinstance(points, list):
                continue
            for pt in points:
                if not isinstance(pt, dict):
                    continue
                lat = pt.get("lat")
                lon = pt.get("lon")
                det_at = _parse_gfw_date(pt.get("date"))
                if lat is None or lon is None or det_at is None:
                    continue
                try:
                    lat_f = float(lat)
                    lon_f = float(lon)
                    det_count = int(round(float(pt.get("detections", 1))))
                except (TypeError, ValueError):
                    continue

                if det_count <= 0 or not (-90.0 <= lat_f <= 90.0) or not (-180.0 <= lon_f <= 180.0):
                    continue

                rows.append({
                    "source": "gfw_sar",
                    "scene_id": f"{dataset_key}:{det_at.strftime('%Y-%m-%d')}",
                    "detected_at": det_at,
                    "latitude": round(lat_f, 5),
                    "longitude": round(lon_f, 5),
                    "chokepoint_name": chokepoint_name,
                    "estimated_length_m": None,
                    "radar_strength_db": None,
                    "confidence": 0.90 if is_dark else 0.95,
                    "is_dark": is_dark,
                    "matched_mmsi": None,
                    "matched_vessel_name": None,
                    "match_distance_km": None,
                    "detection_count": det_count,
                    "dedup_key": _make_gfw_dedup_key(chokepoint_name, det_at, lat_f, lon_f, is_dark),
                })

    return rows


class GfwSarFetcher:
    """Periodic fetcher for Global Fishing Watch v3 Sentinel-1 SAR vessel detections."""

    def __init__(self) -> None:
        self._logged_missing_token = False

    async def run_periodic(self) -> None:
        """Fetch GFW SAR detections on startup and periodically."""
        if not settings.GFW_SAR_ENABLED:
            logger.info("GFW SAR fetcher disabled via GFW_SAR_ENABLED=false.")
            return

        await asyncio.sleep(55)
        interval = max(1, int(settings.GFW_SAR_RUN_INTERVAL_HOURS)) * 3600
        while True:
            try:
                await self.fetch_and_store()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"GfwSarFetcher error: {type(e).__name__}: {e}")
                await asyncio.sleep(30 * 60)
                continue
            await asyncio.sleep(interval)

    async def fetch_and_store(self) -> int:
        """Query GFW v3 4Wings API for unmatched (dark) and matched SAR detections."""
        token = (settings.GFW_API_TOKEN or "").strip()
        if not token:
            if not self._logged_missing_token:
                logger.info(
                    "🛰️ GFW_API_TOKEN not configured; skipping Global Fishing Watch SAR detections. "
                    "Get a free token at https://globalfishingwatch.org/our-apis/"
                )
                self._logged_missing_token = True
            return 0

        now_utc = datetime.now(timezone.utc)
        # GFW SAR dataset has a ~5-day processing lag; query up to 4 days ago
        end_dt = now_utc - timedelta(days=4)
        start_dt = end_dt - timedelta(days=int(settings.GFW_SAR_LOOKBACK_DAYS))
        date_range = f"{start_dt.strftime('%Y-%m-%d')},{end_dt.strftime('%Y-%m-%d')}"

        all_rows: list[dict] = []
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        async with httpx.AsyncClient(timeout=45.0) as client:
            for chokepoint_name, bbox in GFW_MONITORED_REGIONS:
                geojson_body = {"geojson": _bbox_to_geojson_polygon(bbox)}
                for is_dark, matched_filter in ((True, "matched='false'"), (False, "matched='true'")):
                    params = {
                        "spatial-resolution": "HIGH",
                        "temporal-resolution": "HOURLY",
                        "datasets[0]": GFW_SAR_DATASET,
                        "date-range": date_range,
                        "format": "JSON",
                        "filters[0]": matched_filter,
                    }
                    try:
                        resp = await client.post(
                            GFW_REPORT_URL,
                            params=params,
                            headers=headers,
                            json=geojson_body,
                        )
                        if resp.status_code == 401:
                            logger.warning("GFW API returned 401 Unauthorized — check GFW_API_TOKEN.")
                            return 0
                        if resp.status_code == 429:
                            logger.warning("GFW API rate limited (429); backing off before next region.")
                            await asyncio.sleep(10)
                            continue
                        resp.raise_for_status()
                        rows = parse_gfw_sar_response(resp.json(), chokepoint_name, is_dark)
                        all_rows.extend(rows)
                    except Exception as e:
                        logger.warning(
                            f"GFW SAR query failed for {chokepoint_name} (dark={is_dark}): {e}"
                        )
                    await asyncio.sleep(1.0)

        if not all_rows:
            return 0

        async with async_session() as session:
            # Batch in chunks of 500 to keep SQL statements compact
            for i in range(0, len(all_rows), 500):
                chunk = all_rows[i : i + 500]
                stmt = pg_insert(SatelliteDetection).values(chunk).on_conflict_do_nothing(
                    index_elements=["dedup_key"]
                )
                await session.execute(stmt)
            await session.commit()

        dark_cnt = sum(1 for r in all_rows if r["is_dark"])
        logger.info(
            f"🛰️ GFW SAR sync complete ({date_range}): {len(all_rows)} detections "
            f"({dark_cnt} dark, {len(all_rows) - dark_cnt} AIS-matched)"
        )
        return len(all_rows)
