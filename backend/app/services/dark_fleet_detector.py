"""Dark fleet detection engine.

Detects tankers that disable their AIS transponder by monitoring
gaps in AIS transmissions. Normal tankers broadcast every 2-10 seconds
while moving — extended silence indicates potential sanctions evasion.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, and_, or_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings
from app.database import async_session
from app.models import Vessel, AISGap, Port
from app.utils.geo import haversine_km

logger = logging.getLogger(__name__)

APP_START_TIME = datetime.now(timezone.utc)


class DarkFleetDetector:
    """Detects vessels that go dark by analyzing AIS transmission gaps."""

    async def run_periodic(self):
        """Run dark fleet detection every 5 minutes."""
        while True:
            try:
                await self.detect()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Dark fleet detection error: {e}")
            await asyncio.sleep(300)  # 5 minutes

    async def detect(self):
        """Scan all vessels for AIS gaps and update dark fleet status."""
        start_time = time.time()
        stats = {"suspicious": 0, "dark": 0, "extended": 0, "resolved": 0}
        now = datetime.now(timezone.utc)

        async with async_session() as session:
            # Get all known vessels
            vessels_q = select(Vessel).where(Vessel.last_seen.isnot(None))
            result = await session.execute(vessels_q)
            vessels = result.scalars().all()

            for vessel in vessels:
                if vessel.last_seen is None:
                    continue
                    
                # Ignore vessels that haven't been seen since the app started
                # This prevents false alarms for the time the app was turned off.
                if vessel.last_seen < APP_START_TIME:
                    continue

                gap_duration = now - vessel.last_seen
                gap_minutes = gap_duration.total_seconds() / 60
                gap_hours = gap_minutes / 60

                # Check if there's an existing open gap for this vessel
                existing_gap_q = select(AISGap).where(
                    and_(
                        AISGap.mmsi == vessel.mmsi,
                        AISGap.gap_end.is_(None),
                    )
                )
                gap_result = await session.execute(existing_gap_q)
                existing_gap = gap_result.scalar_one_or_none()

                if gap_minutes < settings.DARK_FLEET_SUSPICIOUS_MIN:
                    # Normal — if there was an open gap, vessel has reappeared
                    if existing_gap:
                        await self._resolve_gap(session, existing_gap, vessel, now)
                        stats["resolved"] += 1
                elif gap_hours < settings.DARK_FLEET_DARK_HOURS:
                    # Suspicious
                    if not existing_gap:
                        new_gap = AISGap(
                            mmsi=vessel.mmsi,
                            gap_start=vessel.last_seen,
                            last_known_lat=vessel.last_lat,
                            last_known_lon=vessel.last_lon,
                            status="monitoring",
                        )
                        session.add(new_gap)
                        stats["suspicious"] += 1
                        logger.debug(f"AIS gap detected: {vessel.name} (MMSI={vessel.mmsi}), {gap_minutes:.0f} min")
                elif gap_hours < settings.DARK_FLEET_EXTENDED_HOURS:
                    # Dark
                    if existing_gap and existing_gap.status != "dark":
                        existing_gap.status = "dark"
                        stats["dark"] += 1
                        logger.debug(f"DARK FLEET: {vessel.name} (MMSI={vessel.mmsi}), dark for {gap_hours:.1f}h")
                    elif not existing_gap:
                        new_gap = AISGap(
                            mmsi=vessel.mmsi,
                            gap_start=vessel.last_seen,
                            last_known_lat=vessel.last_lat,
                            last_known_lon=vessel.last_lon,
                            status="dark",
                        )
                        session.add(new_gap)
                        stats["dark"] += 1
                else:
                    # Extended dark
                    if existing_gap and existing_gap.status != "extended_dark":
                        existing_gap.status = "extended_dark"
                        stats["extended"] += 1
                        logger.debug(
                            f"EXTENDED DARK: {vessel.name} (MMSI={vessel.mmsi}), "
                            f"dark for {gap_hours:.1f}h!"
                        )
                    elif not existing_gap:
                        new_gap = AISGap(
                            mmsi=vessel.mmsi,
                            gap_start=vessel.last_seen,
                            last_known_lat=vessel.last_lat,
                            last_known_lon=vessel.last_lon,
                            status="extended_dark",
                        )
                        session.add(new_gap)
                        stats["extended"] += 1

            await session.commit()

        duration = time.time() - start_time
        if any(stats.values()):
            logger.info(f"DarkFleet scan complete in {duration:.1f}s — New: {stats['suspicious']} suspicious, {stats['dark']} dark, {stats['extended']} extended. Resolved: {stats['resolved']}")
        else:
            logger.info(f"DarkFleet scan complete in {duration:.1f}s — No new events.")

    async def _resolve_gap(self, session, gap: AISGap, vessel: Vessel, now: datetime):
        """Resolve an AIS gap when the vessel reappears."""
        gap.gap_end = now
        gap.reappear_lat = vessel.last_lat
        gap.reappear_lon = vessel.last_lon
        gap.status = "resolved"

        # Calculate distance jumped
        if (
            gap.last_known_lat is not None
            and gap.last_known_lon is not None
            and vessel.last_lat is not None
            and vessel.last_lon is not None
        ):
            distance = haversine_km(
                gap.last_known_lat, gap.last_known_lon,
                vessel.last_lat, vessel.last_lon,
            )
            gap.distance_jumped_km = round(distance, 2)

            # Check for spoofing — impossible speed
            gap_duration_hours = (now - gap.gap_start).total_seconds() / 3600
            if gap_duration_hours > 0:
                implied_speed_knots = (distance / 1.852) / gap_duration_hours
                if implied_speed_knots > 25:
                    gap.spoofing_suspected = True
                    logger.warning(
                        f"SPOOFING SUSPECTED: {vessel.name} (MMSI={vessel.mmsi}) "
                        f"jumped {distance:.0f}km in {gap_duration_hours:.1f}h "
                        f"= {implied_speed_knots:.0f} knots (impossible)"
                    )

        # Check if reappeared near a port
        if vessel.last_lat and vessel.last_lon:
            ports_q = select(Port)
            ports_result = await session.execute(ports_q)
            ports = ports_result.scalars().all()

            for port in ports:
                dist = haversine_km(
                    vessel.last_lat, vessel.last_lon,
                    port.latitude, port.longitude,
                )
                if dist < 10:
                    gap.near_port_on_reappear = port.name
                    logger.info(
                        f"COVERT PORT VISIT: {vessel.name} reappeared near {port.name}"
                    )
                    break

        logger.debug(
            f"AIS gap resolved: {vessel.name} (MMSI={vessel.mmsi}), "
            f"distance_jumped={gap.distance_jumped_km}km"
        )
