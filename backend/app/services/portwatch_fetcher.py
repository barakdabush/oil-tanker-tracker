"""IMF PortWatch chokepoint daily transit fetcher.

Fetches satellite-AIS daily transit counts and capacity estimates from the
public IMF PortWatch ArcGIS FeatureServer (no API key required) and upserts
them into portwatch_chokepoint_daily.
"""

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import async_session
from app.models.chokepoint import PortWatchChokepointDaily

logger = logging.getLogger(__name__)

PORTWATCH_QUERY_URL = (
    "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/ArcGIS/rest/services/"
    "Daily_Chokepoints_Data/FeatureServer/0/query"
)
RUN_INTERVAL_SECONDS = 6 * 60 * 60
LOOKBACK_DAYS = 400


def _parse_arcgis_date(val) -> Optional[date]:
    """Convert an ArcGIS epoch-ms timestamp or ISO string into a Python date."""
    if val is None:
        return None
    if isinstance(val, (int, float)):
        return datetime.fromtimestamp(val / 1000.0, tz=timezone.utc).date()
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return date.fromisoformat(val[:10])
            except ValueError:
                return None
    return None


def _parse_portwatch_feature(attrs: dict) -> Optional[dict]:
    """Normalize one ArcGIS feature attributes dict into a DB row dict."""
    portid = (attrs.get("portid") or "").strip()
    portname = (attrs.get("portname") or "").strip()
    row_date = _parse_arcgis_date(attrs.get("date"))
    if not portid or not portname or row_date is None:
        return None

    n_tanker = int(attrs.get("n_tanker") or 0)
    n_container = int(attrs.get("n_container") or 0)
    n_cargo = int(
        attrs.get("n_cargo")
        or (int(attrs.get("n_dry_bulk") or 0) + int(attrs.get("n_general_cargo") or 0) + int(attrs.get("n_roro") or 0))
    )
    n_total = int(attrs.get("n_total") or (n_tanker + n_cargo + n_container))

    cap_tanker = attrs.get("capacity_tanker")
    cap_total = attrs.get("capacity") if attrs.get("capacity") is not None else attrs.get("capacity_total")

    return {
        "portid": portid,
        "date": row_date,
        "portname": portname,
        "n_total": n_total,
        "n_tanker": n_tanker,
        "n_cargo": n_cargo,
        "n_container": n_container,
        "capacity_tanker": float(cap_tanker) if cap_tanker is not None else None,
        "capacity_total": float(cap_total) if cap_total is not None else None,
        "updated_at": datetime.now(timezone.utc),
    }


class PortWatchFetcher:
    """Periodic fetcher for IMF PortWatch Daily Chokepoints Data."""

    async def run_periodic(self):
        """Fetch PortWatch data on startup and then every 6 hours."""
        await asyncio.sleep(15)
        while True:
            try:
                await self.fetch_and_store()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"IMF PortWatch fetch error: {e}")
                await asyncio.sleep(10 * 60)
                continue
            await asyncio.sleep(RUN_INTERVAL_SECONDS)

    async def fetch_and_store(self, lookback_days: int = LOOKBACK_DAYS) -> int:
        """Query IMF PortWatch ArcGIS FeatureServer and upsert daily chokepoint rows."""
        cutoff_date = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).date()
        rows: list[dict] = []
        offset = 0
        page_size = 2000
        max_pages = 10

        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            for _ in range(max_pages):
                params = {
                    "where": "1=1",
                    "outFields": "*",
                    "orderByFields": "date DESC",
                    "resultOffset": offset,
                    "resultRecordCount": page_size,
                    "f": "json",
                }
                resp = await client.get(PORTWATCH_QUERY_URL, params=params)
                resp.raise_for_status()
                payload = resp.json()

                if "error" in payload:
                    logger.error(f"IMF PortWatch ArcGIS error: {payload['error']}")
                    break

                features = payload.get("features") or []
                if not features:
                    break

                reached_cutoff = False
                for feat in features:
                    parsed = _parse_portwatch_feature(feat.get("attributes") or {})
                    if parsed is None:
                        continue
                    if parsed["date"] < cutoff_date:
                        reached_cutoff = True
                        continue
                    rows.append(parsed)

                if reached_cutoff or not payload.get("exceededTransferLimit") or len(features) < page_size:
                    break
                offset += len(features)

        if not rows:
            logger.warning("IMF PortWatch returned 0 rows.")
            return 0

        # Deduplicate on (portid, date) in case pagination overlaps
        deduped = {(r["portid"], r["date"]): r for r in rows}
        batch = list(deduped.values())

        async with async_session() as session:
            chunk_size = 1000
            for i in range(0, len(batch), chunk_size):
                chunk = batch[i : i + chunk_size]
                stmt = pg_insert(PortWatchChokepointDaily).values(chunk)
                upsert = stmt.on_conflict_do_update(
                    index_elements=["portid", "date"],
                    set_={
                        "portname": stmt.excluded.portname,
                        "n_total": stmt.excluded.n_total,
                        "n_tanker": stmt.excluded.n_tanker,
                        "n_cargo": stmt.excluded.n_cargo,
                        "n_container": stmt.excluded.n_container,
                        "capacity_tanker": stmt.excluded.capacity_tanker,
                        "capacity_total": stmt.excluded.capacity_total,
                        "updated_at": stmt.excluded.updated_at,
                    },
                )
                await session.execute(upsert)
            await session.commit()

        logger.info(f"IMF PortWatch: upserted {len(batch)} daily chokepoint rows.")
        return len(batch)
