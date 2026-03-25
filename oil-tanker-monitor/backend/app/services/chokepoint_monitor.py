"""Chokepoint monitoring engine.

Tracks tanker transits through critical maritime chokepoints
(Hormuz, Suez, Malacca, etc.) and detects congestion.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, and_, func, text

from app.config import settings
from app.database import async_session
from app.models import Vessel, Chokepoint, ChokepointTransit

logger = logging.getLogger(__name__)

# Track which vessels are currently inside each chokepoint
# Key: (mmsi, chokepoint_id), Value: transit_start_time
_vessels_in_chokepoint: dict[tuple[int, int], datetime] = {}


class ChokepointMonitor:
    """Monitors tanker traffic through maritime chokepoints."""

    async def run_periodic(self):
        """Run chokepoint monitoring every 2 minutes."""
        while True:
            try:
                await self.monitor()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Chokepoint monitoring error: {e}")
            await asyncio.sleep(120)  # 2 minutes

    async def monitor(self):
        """Check all vessel positions against chokepoint polygons."""
        start_time = time.time()
        stats = {"entries": 0, "exits": 0, "congestion": 0, "low": 0}

        async with async_session() as session:
            # Get all chokepoints
            cp_q = select(Chokepoint)
            cp_result = await session.execute(cp_q)
            chokepoints = cp_result.scalars().all()

            if not chokepoints:
                return

            # Get vessels with recent positions
            cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)
            vessels_q = select(Vessel).where(
                and_(
                    Vessel.last_seen > cutoff,
                    Vessel.last_lat.isnot(None),
                    Vessel.last_lon.isnot(None),
                )
            )
            v_result = await session.execute(vessels_q)
            vessels = v_result.scalars().all()

            now = datetime.now(timezone.utc)

            for vessel in vessels:
                for cp in chokepoints:
                    # Check if vessel position is inside the chokepoint polygon
                    inside_q = select(func.count()).where(
                        text(f"""
                            ST_Contains(
                                (SELECT boundary_polygon FROM chokepoints WHERE id = {cp.id}),
                                ST_SetSRID(ST_MakePoint({vessel.last_lon}, {vessel.last_lat}), 4326)
                            )
                        """)
                    )
                    inside_result = await session.execute(inside_q)
                    is_inside = inside_result.scalar() > 0

                    key = (vessel.mmsi, cp.id)

                    if is_inside and key not in _vessels_in_chokepoint:
                        # Vessel ENTERED the chokepoint
                        _vessels_in_chokepoint[key] = now

                        # Determine direction from vessel course
                        direction = self._determine_direction(vessel.last_course)

                        transit = ChokepointTransit(
                            time=now,
                            chokepoint_id=cp.id,
                            mmsi=vessel.mmsi,
                            entry_time=now,
                            direction=direction,
                            draft_on_entry=vessel.draft,
                        )
                        session.add(transit)
                        stats["entries"] += 1

                        logger.debug(
                            f"Chokepoint ENTER: {vessel.name} entered {cp.name} "
                            f"({direction}), draft={vessel.draft}"
                        )

                    elif not is_inside and key in _vessels_in_chokepoint:
                        # Vessel EXITED the chokepoint
                        entry_time = _vessels_in_chokepoint.pop(key)
                        transit_duration = (now - entry_time).total_seconds() / 60

                        # Update the transit record
                        transit_q = select(ChokepointTransit).where(
                            and_(
                                ChokepointTransit.mmsi == vessel.mmsi,
                                ChokepointTransit.chokepoint_id == cp.id,
                                ChokepointTransit.exit_time.is_(None),
                            )
                        ).order_by(ChokepointTransit.entry_time.desc()).limit(1)

                        t_result = await session.execute(transit_q)
                        transit = t_result.scalar_one_or_none()

                        if transit:
                            transit.exit_time = now
                            transit.transit_duration_min = round(transit_duration, 1)

                        stats["exits"] += 1
                        logger.debug(
                            f"Chokepoint EXIT: {vessel.name} exited {cp.name} "
                            f"after {transit_duration:.0f} min"
                        )

            # Congestion check
            for cp in chokepoints:
                current_count = sum(
                    1 for k in _vessels_in_chokepoint if k[1] == cp.id
                )
                threshold = cp.congestion_threshold or 20

                if current_count > threshold * 1.5:
                    stats["congestion"] += 1
                    logger.debug(
                        f"CONGESTION: {cp.name} has {current_count} vessels "
                        f"(threshold: {threshold})"
                    )
                elif current_count < threshold * 0.5 and current_count > 0:
                    stats["low"] += 1
                    logger.debug(
                        f"UNUSUALLY LOW: {cp.name} has only {current_count} vessels "
                        f"(threshold: {threshold}) — possible disruption"
                    )

            await session.commit()

        duration = time.time() - start_time
        if any(stats.values()):
            logger.info(f"Chokepoint scan complete in {duration:.1f}s — Active Transits: {len(_vessels_in_chokepoint)}. New Entries: {stats['entries']}, New Exits: {stats['exits']}, Congestion Warns: {stats['congestion']}, Low Warns: {stats['low']}")
        else:
            logger.info(f"Chokepoint scan complete in {duration:.1f}s — No new events. Active Transits: {len(_vessels_in_chokepoint)}")

    @staticmethod
    def _determine_direction(course: float | None) -> str:
        """Determine compass direction from course angle."""
        if course is None:
            return "unknown"
        if 315 <= course or course < 45:
            return "northbound"
        elif 45 <= course < 135:
            return "eastbound"
        elif 135 <= course < 225:
            return "southbound"
        else:
            return "westbound"
