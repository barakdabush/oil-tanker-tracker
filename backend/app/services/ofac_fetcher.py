"""US Treasury OFAC SDN sanctioned vessels fetcher.

Downloads the public OFAC SDN.CSV export (no API key required), extracts vessel
entries with IMO/MMSI identifiers from remarks, and syncs them to
ofac_sanctioned_vessels.
"""

import asyncio
import csv
import io
import logging
import re
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import async_session
from app.models.sanctions import OfacSanctionedVessel

logger = logging.getLogger(__name__)

OFAC_SDN_CSV_URL = "https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/SDN.CSV"
RUN_INTERVAL_SECONDS = 12 * 60 * 60

IMO_RE = re.compile(r"\bIMO\s*[:#]?\s*(\d{7})\b", re.IGNORECASE)
MMSI_RE = re.compile(r"\bMMSI\s*[:#]?\s*(\d{9})\b", re.IGNORECASE)


def _clean_ofac_val(val: Optional[str]) -> Optional[str]:
    if not val:
        return None
    cleaned = val.strip()
    if cleaned in ("", "-0-"):
        return None
    return cleaned


def parse_ofac_sdn_csv(csv_text: str) -> list[dict]:
    """Parse OFAC SDN.CSV content and return normalized vessel records."""
    now = datetime.now(timezone.utc)
    reader = csv.reader(io.StringIO(csv_text))
    vessels: list[dict] = []

    for row in reader:
        # OFAC SDN.CSV has 12 columns:
        # 0: ent_num, 1: SDN_Name, 2: SDN_Type, 3: Program, 4: Title,
        # 5: Call_Sign, 6: Vess_type, 7: Tonnage, 8: GRT, 9: Vess_flag,
        # 10: Vess_owner, 11: Remarks
        if len(row) < 12:
            continue

        sdn_type = (_clean_ofac_val(row[2]) or "").lower()
        if sdn_type != "vessel":
            continue

        try:
            uid = int(row[0].strip())
        except (ValueError, AttributeError):
            continue

        name = _clean_ofac_val(row[1])
        if not name:
            continue

        program = _clean_ofac_val(row[3])
        vessel_type = _clean_ofac_val(row[6])
        flag = _clean_ofac_val(row[9])
        remarks = _clean_ofac_val(row[11])

        imo = None
        mmsi = None
        if remarks:
            imo_match = IMO_RE.search(remarks)
            if imo_match:
                imo = int(imo_match.group(1))
            mmsi_match = MMSI_RE.search(remarks)
            if mmsi_match:
                mmsi = int(mmsi_match.group(1))

        vessels.append({
            "uid": uid,
            "imo": imo,
            "mmsi": mmsi,
            "name": name[:255],
            "sdn_type": "vessel",
            "program": program[:128] if program else None,
            "flag": flag[:64] if flag else None,
            "vessel_type": vessel_type[:64] if vessel_type else None,
            "remarks": remarks[:1000] if remarks else None,
            "updated_at": now,
        })

    return vessels


class OfacSanctionsFetcher:
    """Periodic fetcher for US Treasury OFAC SDN sanctioned vessels."""

    async def run_periodic(self):
        """Sync OFAC SDN vessels on startup and then every 12 hours."""
        await asyncio.sleep(25)
        while True:
            try:
                await self.fetch_and_store()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"OFAC SDN fetch error: {e}")
                await asyncio.sleep(15 * 60)
                continue
            await asyncio.sleep(RUN_INTERVAL_SECONDS)

    async def fetch_and_store(self) -> int:
        """Download OFAC SDN.CSV and upsert vessel entries."""
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.get(OFAC_SDN_CSV_URL, headers={"User-Agent": "OilTankerTracker/1.0"})
            resp.raise_for_status()
            csv_text = resp.text

        vessels = parse_ofac_sdn_csv(csv_text)
        if not vessels:
            logger.warning("OFAC SDN.CSV parsed 0 vessels; skipping DB update.")
            return 0

        active_uids = [v["uid"] for v in vessels]

        async with async_session() as session:
            chunk_size = 1000
            for i in range(0, len(vessels), chunk_size):
                chunk = vessels[i : i + chunk_size]
                stmt = pg_insert(OfacSanctionedVessel).values(chunk)
                upsert = stmt.on_conflict_do_update(
                    index_elements=["uid"],
                    set_={
                        "imo": stmt.excluded.imo,
                        "mmsi": stmt.excluded.mmsi,
                        "name": stmt.excluded.name,
                        "sdn_type": stmt.excluded.sdn_type,
                        "program": stmt.excluded.program,
                        "flag": stmt.excluded.flag,
                        "vessel_type": stmt.excluded.vessel_type,
                        "remarks": stmt.excluded.remarks,
                        "updated_at": stmt.excluded.updated_at,
                    },
                )
                await session.execute(upsert)

            # Remove vessels delisted by OFAC
            await session.execute(
                delete(OfacSanctionedVessel).where(OfacSanctionedVessel.uid.notin_(active_uids))
            )
            await session.commit()

        logger.info(f"OFAC SDN: synced {len(vessels)} sanctioned vessels.")
        return len(vessels)
