"""External strait risk & geopolitical signal fetcher (Straits.live + GDELT).

Fetches:
- Straits.live (CC0, no key): Hormuz Crisis Pressure Index, Escalation Probability,
  Persian Gulf stranded/anchored vessel counts, and daily maritime incident counts.
- GDELT DOC 2.0 API (free, no key): Daily news coverage volume & tone for
  Strait of Hormuz / Bab el-Mandeb / Red Sea maritime attacks and tanker disruptions.

Upserts daily rows into strait_risk_daily.
"""

import asyncio
import logging
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Optional

import httpx
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import async_session
from app.models.global_oil_features import StraitRiskDaily
from app.utils.gdelt import gdelt_get_json

logger = logging.getLogger(__name__)

STRAITS_INDEX_URL = "https://straits.live/api/index?history=30d"
STRAITS_STRANDED_URL = "https://straits.live/api/v1/stranded?limit=720"
STRAITS_EVENTS_URL = "https://straits.live/api/v1/events?limit=200"
GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"
GDELT_QUERY = '("Strait of Hormuz" OR "Bab el-Mandeb" OR "Red Sea") (tanker OR shipping OR attack OR blockade)'

RUN_INTERVAL_SECONDS = 6 * 60 * 60


def _parse_iso_date(val) -> Optional[date]:
    if not val:
        return None
    if isinstance(val, (int, float)):
        # Epoch milliseconds or seconds
        ts = val / 1000.0 if val > 1e12 else float(val)
        return datetime.fromtimestamp(ts, tz=timezone.utc).date()
    if isinstance(val, str):
        s = val.strip()
        # GDELT format: YYYYMMDDTHHMMSSZ or YYYYMMDD
        if len(s) >= 8 and s[:8].isdigit() and (len(s) == 8 or s[8] in ("T", " ")):
            try:
                return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
            except ValueError:
                pass
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return date.fromisoformat(s[:10])
            except ValueError:
                return None
    return None


class StraitRiskFetcher:
    """Periodic fetcher for Straits.live and GDELT Hormuz/Bab el-Mandeb risk signals."""

    async def run_periodic(self):
        """Fetch strait risk signals on startup and then every 6 hours."""
        await asyncio.sleep(25)
        while True:
            try:
                await self.fetch_and_store()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"StraitRiskFetcher error: {type(e).__name__}: {e}")
                await asyncio.sleep(10 * 60)
                continue
            await asyncio.sleep(RUN_INTERVAL_SECONDS)

    async def _fetch_straits_index(
        self, client: httpx.AsyncClient, by_date: dict[date, dict]
    ) -> None:
        resp = await client.get(STRAITS_INDEX_URL)
        resp.raise_for_status()
        payload = resp.json()
        indices = payload.get("indices") or {}

        today = _parse_iso_date(payload.get("asOf")) or datetime.now(timezone.utc).date()
        cp = indices.get("crisisPressure") or {}
        ep = indices.get("escalationProbability") or {}

        if cp.get("value") is not None:
            by_date[today]["hormuz_crisis_pressure"] = round(float(cp["value"]), 2)
        if ep.get("value") is not None:
            by_date[today]["hormuz_escalation_prob"] = round(float(ep["value"]), 2)

        for pt in cp.get("history") or []:
            d = _parse_iso_date(pt.get("iso") or pt.get("date") or pt.get("t"))
            if d and pt.get("value") is not None:
                by_date[d]["hormuz_crisis_pressure"] = round(float(pt["value"]), 2)

        for pt in ep.get("history") or []:
            d = _parse_iso_date(pt.get("iso") or pt.get("date") or pt.get("t"))
            if d and pt.get("value") is not None:
                by_date[d]["hormuz_escalation_prob"] = round(float(pt["value"]), 2)

    async def _fetch_straits_stranded(
        self, client: httpx.AsyncClient, by_date: dict[date, dict]
    ) -> None:
        resp = await client.get(STRAITS_STRANDED_URL)
        resp.raise_for_status()
        payload = resp.json()
        buckets: dict[date, list[float]] = defaultdict(list)

        for pt in payload.get("points") or []:
            d = _parse_iso_date(pt.get("iso") or pt.get("asOf") or pt.get("t"))
            val = pt.get("count") if pt.get("count") is not None else pt.get("value")
            if d and val is not None:
                buckets[d].append(float(val))

        for d, vals in buckets.items():
            if vals:
                by_date[d]["hormuz_stranded_vessels"] = round(sum(vals) / len(vals), 1)

    async def _fetch_straits_events(
        self, client: httpx.AsyncClient, by_date: dict[date, dict]
    ) -> None:
        resp = await client.get(STRAITS_EVENTS_URL)
        resp.raise_for_status()
        payload = resp.json()
        counts: dict[date, int] = defaultdict(int)

        for ev in payload.get("events") or []:
            d = _parse_iso_date(ev.get("occurredAt") or ev.get("date"))
            if d:
                counts[d] += 1

        for d, cnt in counts.items():
            by_date[d]["hormuz_incidents_count"] = cnt

    async def _fetch_gdelt_series(
        self, client: httpx.AsyncClient, mode: str, field: str, by_date: dict[date, dict]
    ) -> None:
        params = {
            "query": GDELT_QUERY,
            "mode": mode,
            "timespan": "180d",
            "format": "json",
        }
        payload = await gdelt_get_json(client, params, label=f"StraitRiskFetcher[{mode}]")
        if not payload:
            return
        timeline = payload.get("timeline") or []
        if not timeline:
            return
        series = timeline[0].get("data") or []
        for pt in series:
            d = _parse_iso_date(pt.get("date"))
            val = pt.get("value")
            if d and val is not None:
                by_date[d][field] = round(float(val), 4)

    async def fetch_and_store(self) -> int:
        """Fetch all configured external strait risk series and upsert into strait_risk_daily."""
        by_date: dict[date, dict] = defaultdict(dict)

        headers = {"User-Agent": "oil-tanker-tracker/1.0 (https://github.com/barakdabush/oil-tanker-tracker)"}
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True, headers=headers) as client:
            tasks = [
                ("straits_index", self._fetch_straits_index(client, by_date), 0.0),
                ("straits_stranded", self._fetch_straits_stranded(client, by_date), 0.0),
                ("straits_events", self._fetch_straits_events(client, by_date), 0.0),
                # GDELT DOC 2.0 enforces ~1 req / 5s per IP; space out volume and tone calls.
                ("gdelt_vol", self._fetch_gdelt_series(client, "timelinevol", "gdelt_strait_risk_vol", by_date), 0.0),
                ("gdelt_tone", self._fetch_gdelt_series(client, "timelinetone", "gdelt_strait_tone", by_date), 6.0),
            ]
            for name, coro, pre_delay in tasks:
                if pre_delay > 0:
                    await asyncio.sleep(pre_delay)
                try:
                    await coro
                except Exception as e:
                    logger.warning(f"StraitRiskFetcher[{name}] failed: {type(e).__name__}: {e}")

        if not by_date:
            logger.warning("StraitRiskFetcher returned 0 rows.")
            return 0

        now = datetime.now(timezone.utc)
        rows = [
            {
                "date": d,
                "hormuz_crisis_pressure": vals.get("hormuz_crisis_pressure"),
                "hormuz_escalation_prob": vals.get("hormuz_escalation_prob"),
                "hormuz_stranded_vessels": vals.get("hormuz_stranded_vessels"),
                "hormuz_incidents_count": vals.get("hormuz_incidents_count"),
                "gdelt_strait_risk_vol": vals.get("gdelt_strait_risk_vol"),
                "gdelt_strait_tone": vals.get("gdelt_strait_tone"),
                "fetched_at": now,
            }
            for d, vals in sorted(by_date.items())
        ]

        async with async_session() as session:
            stmt = pg_insert(StraitRiskDaily).values(rows)
            upsert = stmt.on_conflict_do_update(
                index_elements=["date"],
                set_={
                    "hormuz_crisis_pressure": func.coalesce(
                        stmt.excluded.hormuz_crisis_pressure,
                        StraitRiskDaily.hormuz_crisis_pressure,
                    ),
                    "hormuz_escalation_prob": func.coalesce(
                        stmt.excluded.hormuz_escalation_prob,
                        StraitRiskDaily.hormuz_escalation_prob,
                    ),
                    "hormuz_stranded_vessels": func.coalesce(
                        stmt.excluded.hormuz_stranded_vessels,
                        StraitRiskDaily.hormuz_stranded_vessels,
                    ),
                    "hormuz_incidents_count": func.coalesce(
                        stmt.excluded.hormuz_incidents_count,
                        StraitRiskDaily.hormuz_incidents_count,
                    ),
                    "gdelt_strait_risk_vol": func.coalesce(
                        stmt.excluded.gdelt_strait_risk_vol,
                        StraitRiskDaily.gdelt_strait_risk_vol,
                    ),
                    "gdelt_strait_tone": func.coalesce(
                        stmt.excluded.gdelt_strait_tone,
                        StraitRiskDaily.gdelt_strait_tone,
                    ),
                    "fetched_at": stmt.excluded.fetched_at,
                },
            )
            await session.execute(upsert)
            await session.commit()

        logger.info(f"StraitRiskFetcher: upserted {len(rows)} daily risk rows.")
        return len(rows)
