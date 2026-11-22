"""Open-data Strait of Hormuz live tanker position ingestion via hormuz.now (CC BY 4.0).

Fetches https://hormuz.now/api/snapshot?format=compact (no API key required)
covering 24°N–28.5°N, 54°E–59°E (~400+ live tankers) and normalizes tanker rows
into our shared AIS ingestion pipeline with ETag caching.
"""

import asyncio
import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

SYNTHETIC_MMSI_BASE = 990_000_000
SYNTHETIC_MMSI_MOD = 9_999_999
MAX_AGE_MINUTES = 360  # Ignore positions older than 6 hours

_NON_ALNUM_RE = re.compile(r"[^A-Z0-9]+")


def normalize_vessel_name(name: Optional[str]) -> str:
    """Normalize a vessel name for cross-source matching (uppercase alphanumerics only)."""
    if not name:
        return ""
    return _NON_ALNUM_RE.sub("", name.upper()).strip()


def synthetic_mmsi_for_hormuz_id(ext_id: str) -> int:
    """Return a deterministic MMSI in the reserved 990M-999M range (outside ITU 200M-799M ship MMSIs)."""
    raw = str(ext_id).strip()
    if raw.isdigit():
        return SYNTHETIC_MMSI_BASE + (int(raw) % SYNTHETIC_MMSI_MOD)
    digest = int(hashlib.sha1(raw.encode("utf-8")).hexdigest()[:8], 16)
    return SYNTHETIC_MMSI_BASE + (digest % SYNTHETIC_MMSI_MOD)


def _is_tanker_row(row: dict) -> bool:
    """Return True if a hormuz.now compact row represents a tanker."""
    cat = str(row.get("category") or "").lower()
    vtype = str(row.get("type") or "").lower()
    if cat == "tanker":
        return True
    if "tanker" in vtype or "oil" in vtype or "lng" in vtype or "lpg" in vtype:
        return True
    return False


def _infer_ais_ship_type(vtype: str) -> int:
    """Map hormuz.now descriptive vessel type to an AIS tanker code (80-89)."""
    t = (vtype or "").lower()
    if "chemical" in t:
        return 82
    if "lng" in t or "lpg" in t or "gas" in t:
        return 84
    if "crude" in t:
        return 80
    if "product" in t or "oil" in t:
        return 81
    return 80


def parse_hormuz_snapshot(payload: dict) -> list[dict]:
    """Parse a hormuz.now compact snapshot JSON into normalized tanker position dicts."""
    fields = payload.get("fields")
    rows = payload.get("rows")
    if not isinstance(fields, list) or not isinstance(rows, list):
        return []

    snap_ms = payload.get("t")
    if isinstance(snap_ms, (int, float)) and snap_ms > 1e12:
        snap_time = datetime.fromtimestamp(snap_ms / 1000.0, tz=timezone.utc)
    else:
        snap_time = datetime.now(timezone.utc)

    tankers: list[dict] = []
    for raw_row in rows:
        if not isinstance(raw_row, list) or len(raw_row) != len(fields):
            continue
        row = dict(zip(fields, raw_row))
        if not _is_tanker_row(row):
            continue

        ext_id = row.get("id")
        lat = row.get("lat")
        lon = row.get("lon")
        if ext_id is None or lat is None or lon is None:
            continue

        try:
            lat_f = float(lat)
            lon_f = float(lon)
        except (TypeError, ValueError):
            continue

        if not (-90.0 <= lat_f <= 90.0) or not (-180.0 <= lon_f <= 180.0):
            continue

        age_min = row.get("ageMin")
        try:
            age_f = float(age_min) if age_min is not None else 0.0
        except (TypeError, ValueError):
            age_f = 0.0

        if age_f < 0 or age_f > MAX_AGE_MINUTES:
            continue

        pos_time = snap_time - timedelta(minutes=age_f)

        speed = row.get("speed")
        course = row.get("course")
        heading = row.get("heading")
        length = row.get("length")
        dwt = row.get("dwt")
        flag = str(row.get("flag") or "").strip()
        if flag in ("--", ""):
            flag = None

        tankers.append({
            "ext_id": str(ext_id),
            "name": (str(row.get("name") or "").strip() or None),
            "lat": lat_f,
            "lon": lon_f,
            "speed": float(speed) if speed is not None else None,
            "course": float(course) if course is not None else None,
            "heading": float(heading) if heading is not None and float(heading) <= 360 else None,
            "ship_type": _infer_ais_ship_type(str(row.get("type") or "")),
            "length": float(length) if length and float(length) > 0 else None,
            "dwt": float(dwt) if dwt and float(dwt) > 0 else None,
            "flag": flag[:10] if flag else None,
            "destination": (str(row.get("destination") or "").strip()[:255] or None),
            "timestamp": pos_time,
        })

    return tankers


async def poll_hormuz_now_forever(
    on_snapshot: Callable[[list[dict]], Awaitable[None]],
) -> None:
    """Continuously poll hormuz.now live snapshot with ETag caching and backoff."""
    url = settings.HORMUZ_NOW_SNAPSHOT_URL
    interval = max(20, int(settings.HORMUZ_NOW_POLL_SECONDS))
    etag: Optional[str] = None
    headers_base = {
        "User-Agent": "OilTankerTracker/1.0 (https://github.com/barakdabush/oil-tanker-tracker)",
        "Accept": "application/json",
    }

    logger.info(f"Starting hormuz.now live tanker poller ({url}, every {interval}s)")

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        while True:
            headers = dict(headers_base)
            if etag:
                headers["If-None-Match"] = etag

            resp = await client.get(url, headers=headers)
            if resp.status_code == 304:
                await asyncio.sleep(interval)
                continue

            resp.raise_for_status()
            if resp.headers.get("etag"):
                etag = resp.headers["etag"]

            payload = resp.json()
            tankers = parse_hormuz_snapshot(payload)
            if tankers:
                await on_snapshot(tankers)

            await asyncio.sleep(interval)
