"""Ship-to-Ship (STS) transfer detection engine.

Detects when two tankers meet in open water to transfer oil — a common
method to obscure oil origin in sanctioned trade. Uses proximity analysis
combined with speed and draft change monitoring.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, and_, or_

from app.config import settings
from app.database import async_session
from app.models import Vessel, STSEvent, Port, AISGap
from app.utils.geo import haversine_km

logger = logging.getLogger(__name__)


# Track ongoing STS encounters: key = frozenset({mmsi_a, mmsi_b})
_active_encounters: dict[frozenset, dict] = {}


class STSDetector:
    """Detects ship-to-ship transfer events between tankers."""

    async def run_periodic(self):
        """Run STS detection every 10 minutes."""
        while True:
            try:
               await self.detect()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"STS detection error: {e}")
            await asyncio.sleep(600)  # 10 minutes

    async def detect(self):
        """Find tanker pairs in close proximity far from ports."""
        start_time = time.time()
        stats = {"encounters": 0, "confirmed": 0}
        async with async_session() as session:
            # Get all vessels with recent positions that are nearly stationary
            cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
            vessels_q = select(Vessel).where(
                and_(
                    Vessel.last_seen > cutoff,
                    Vessel.last_lat.isnot(None),
                    Vessel.last_lon.isnot(None),
                )
            )
            result = await session.execute(vessels_q)
            vessels = result.scalars().all()

            # Get ports for distance check
            ports_q = select(Port)
            ports_result = await session.execute(ports_q)
            ports = ports_result.scalars().all()

            # Find pairs of tankers within 500m of each other
            slow_vessels = [v for v in vessels if (v.last_speed or 0) < 3]

            found_pairs = set()

            # --- O(N) Spatial Grid Optimization ---
            # Group vessels into ~1.1km integer buckets (1/100th of a degree)
            # This turns a ~80M connection check into fewer than ~2000!
            grid = {}
            for v in slow_vessels:
                if v.last_lat is None or v.last_lon is None:
                    continue
                lat_idx = int(v.last_lat * 100)
                lon_idx = int(v.last_lon * 100)
                grid.setdefault((lat_idx, lon_idx), []).append(v)
            
            for (lat_idx, lon_idx), cell_vessels in grid.items():
                # Check current cell and 8 adjacent cells to catch border interactions
                for dlat in (-1, 0, 1):
                    for dlon in (-1, 0, 1):
                        adj_cell = (lat_idx + dlat, lon_idx + dlon)
                        if adj_cell not in grid:
                            continue
                            
                        for va in cell_vessels:
                            for vb in grid[adj_cell]:
                                if va.mmsi >= vb.mmsi:
                                    continue  # Ensure we only check each combo once
                                
                                dist_km = haversine_km(
                                    va.last_lat, va.last_lon,
                                    vb.last_lat, vb.last_lon,
                                )
                                dist_m = dist_km * 1000

                                if dist_m < settings.STS_PROXIMITY_M:
                                    pair_key = frozenset({va.mmsi, vb.mmsi})
                                    if pair_key in found_pairs:
                                        continue
                                    
                                    found_pairs.add(pair_key)

                                    # Check port distance (heavy, but now only on found pairs!)
                                    min_port_dist = float("inf")
                                    for port in ports:
                                        pd = haversine_km(
                                            va.last_lat, va.last_lon,
                                            port.latitude, port.longitude,
                                        )
                                        min_port_dist = min(min_port_dist, pd)

                                    if min_port_dist < settings.STS_PORT_DISTANCE_KM:
                                        continue  # Near port, skip

                                    if pair_key not in _active_encounters:
                                        # New potential STS event
                                        _active_encounters[pair_key] = {
                                            "vessel_a_mmsi": min(va.mmsi, vb.mmsi),
                                            "vessel_b_mmsi": max(va.mmsi, vb.mmsi),
                                            "start_time": datetime.now(timezone.utc),
                                            "lat": (va.last_lat + vb.last_lat) / 2,
                                            "lon": (va.last_lon + vb.last_lon) / 2,
                                            "distance_from_port_km": min_port_dist,
                                            "va_initial_draft": va.draft,
                                            "vb_initial_draft": vb.draft,
                                        }
                                        stats["encounters"] += 1
                                        logger.debug(
                                            f"STS encounter detected: {va.name} + {vb.name}, "
                                            f"{dist_m:.0f}m apart, {min_port_dist:.0f}km from port"
                                        )

            # Check for encounters that ended (vessels separated)
            ended_pairs = set(_active_encounters.keys()) - found_pairs

            for pair_key in ended_pairs:
                encounter = _active_encounters.pop(pair_key)
                duration = (datetime.now(timezone.utc) - encounter["start_time"]).total_seconds() / 60

                if duration < settings.STS_MIN_DURATION_MIN:
                    continue  # Too brief

                # Calculate confidence
                confidence = 0.0
                if encounter["distance_from_port_km"] > 20:
                    confidence += 0.3
                if duration > 120:  # > 2 hours
                    confidence += 0.2

                # Check draft changes (need to get current vessel data)
                mmsi_a = encounter["vessel_a_mmsi"]
                mmsi_b = encounter["vessel_b_mmsi"]

                va_result = await session.execute(select(Vessel).where(Vessel.mmsi == mmsi_a))
                vb_result = await session.execute(select(Vessel).where(Vessel.mmsi == mmsi_b))
                va = va_result.scalar_one_or_none()
                vb = vb_result.scalar_one_or_none()

                va_draft_change = None
                vb_draft_change = None

                if va and encounter.get("va_initial_draft") and va.draft:
                    va_draft_change = va.draft - encounter["va_initial_draft"]
                if vb and encounter.get("vb_initial_draft") and vb.draft:
                    vb_draft_change = vb.draft - encounter["vb_initial_draft"]

                # Inverse correlation = one loaded, one unloaded
                if va_draft_change and vb_draft_change:
                    if (va_draft_change > 0 and vb_draft_change < 0) or \
                       (va_draft_change < 0 and vb_draft_change > 0):
                        confidence += 0.3

                # Check if either vessel has dark fleet history
                for mmsi in [mmsi_a, mmsi_b]:
                    gap_q = select(AISGap).where(AISGap.mmsi == mmsi).limit(1)
                    gap_result = await session.execute(gap_q)
                    if gap_result.scalar_one_or_none():
                        confidence += 0.2
                        break

                confidence = min(confidence, 1.0)

                if confidence > 0.6:
                    stats["confirmed"] += 1

                sts_event = STSEvent(
                    vessel_a_mmsi=mmsi_a,
                    vessel_b_mmsi=mmsi_b,
                    start_time=encounter["start_time"],
                    end_time=datetime.now(timezone.utc),
                    lat=encounter["lat"],
                    lon=encounter["lon"],
                    distance_from_port_km=encounter["distance_from_port_km"],
                    vessel_a_draft_change=va_draft_change,
                    vessel_b_draft_change=vb_draft_change,
                    status="confirmed" if confidence > 0.6 else "detected",
                    confidence=round(confidence, 2),
                )
                session.add(sts_event)

                logger.debug(
                    f"STS EVENT: vessels {mmsi_a} + {mmsi_b}, "
                    f"duration={duration:.0f}min, confidence={confidence:.2f}"
                )

            await session.commit()

        duration = time.time() - start_time
        if any(stats.values()):
            logger.info(f"STS scan complete in {duration:.1f}s — Active Close Encounters: {len(_active_encounters)}. New Encounters: {stats['encounters']}, New Confirmed STS: {stats['confirmed']}")
        else:
             logger.info(f"STS scan complete in {duration:.1f}s — No new events. Active Encounters: {len(_active_encounters)}")
