"""Cargo load/unload detection engine.

Detects loading and unloading events by monitoring tanker draft changes
during port visits. When a tanker becomes stationary near a port, we track
its draft. When it departs, the draft change indicates loading (+) or
unloading (-).
"""

import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta

from sqlalchemy import select, and_, func, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings
from app.database import async_session
from app.models import Vessel, VesselPosition, Port, CargoEvent
from app.utils.geo import haversine_km
from app.utils.cargo import estimate_cargo_volume

logger = logging.getLogger(__name__)


# In-memory state for tracking port visits
# Key: mmsi, Value: {port_id, port_name, arrival_time, arrival_draft, lat, lon}
_active_port_visits: dict[int, dict] = {}


class CargoDetector:
    """Detects cargo loading and unloading events at ports."""

    async def run_periodic(self):
        """Run cargo detection every 60 seconds."""
        while True:
            try:
                await self.detect()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cargo detection error: {e}")
            await asyncio.sleep(60)

    async def detect(self):
        """Check all vessels for port proximity and draft changes."""
        start_time = time.time()
        stats = {"loading": 0, "unloading": 0}
        async with async_session() as session:
            # Get all vessels with recent positions
            vessels_q = select(Vessel).where(
                Vessel.last_seen > datetime.now(timezone.utc) - timedelta(hours=1)
            )
            result = await session.execute(vessels_q)
            vessels = result.scalars().all()

            # Get all ports
            ports_q = select(Port)
            ports_result = await session.execute(ports_q)
            ports = ports_result.scalars().all()

            for vessel in vessels:
                if vessel.last_lat is None or vessel.last_lon is None:
                    continue

                await self._check_vessel(session, vessel, ports, stats)

            await session.commit()

        duration = time.time() - start_time
        if any(stats.values()):
            logger.info(f"Cargo scan complete in {duration:.1f}s — Active Port Visits: {len(_active_port_visits)}. New Loading: {stats['loading']}, New Unloading: {stats['unloading']}")
        else:
            logger.info(f"Cargo scan complete in {duration:.1f}s — No new events. Active Port Visits: {len(_active_port_visits)}")

    async def _check_vessel(self, session, vessel: Vessel, ports: list[Port], stats: dict):
        """Check if vessel is near a port and track its state."""
        mmsi = vessel.mmsi
        speed = vessel.last_speed or 0

        # Find nearest port
        nearest_port = None
        min_distance = float("inf")

        for port in ports:
            dist = haversine_km(
                vessel.last_lat, vessel.last_lon,
                port.latitude, port.longitude,
            )
            if dist < min_distance:
                min_distance = dist
                nearest_port = port

        is_near_port = min_distance < settings.PORT_PROXIMITY_KM
        is_stationary = speed < settings.STATIONARY_SPEED_KNOTS

        if mmsi not in _active_port_visits:
            # Not currently in a port visit
            if is_near_port and is_stationary and nearest_port:
                # Start a new port visit session
                _active_port_visits[mmsi] = {
                    "port_id": nearest_port.id,
                    "port_name": nearest_port.name,
                    "arrival_time": datetime.now(timezone.utc),
                    "arrival_draft": vessel.draft,
                    "lat": vessel.last_lat,
                    "lon": vessel.last_lon,
                }
                logger.debug(f"Port visit started: {vessel.name} (MMSI={mmsi}) at {nearest_port.name}")
        else:
            # Currently in a port visit
            visit = _active_port_visits[mmsi]

            if speed > settings.DEPARTURE_SPEED_KNOTS or not is_near_port:
                # Vessel has departed — check draft change
                departure_draft = vessel.draft
                arrival_draft = visit.get("arrival_draft")

                if arrival_draft is not None and departure_draft is not None:
                    draft_change = departure_draft - arrival_draft

                    if abs(draft_change) > settings.DRAFT_CHANGE_THRESHOLD_M:
                        event_type = "loading" if draft_change > 0 else "unloading"
                        volume = estimate_cargo_volume(
                            vessel.length, vessel.beam, draft_change,
                        )

                        cargo_event = CargoEvent(
                            mmsi=mmsi,
                            port_id=visit["port_id"],
                            event_type=event_type,
                            arrival_time=visit["arrival_time"],
                            departure_time=datetime.now(timezone.utc),
                            draft_arrival=arrival_draft,
                            draft_departure=departure_draft,
                            draft_change=draft_change,
                            estimated_volume_barrels=volume,
                            confidence=0.8 if abs(draft_change) > 1.0 else 0.5,
                        )
                        session.add(cargo_event)

                        if event_type == "loading":
                            stats["loading"] += 1
                        else:
                            stats["unloading"] += 1

                        logger.debug(
                            f"Cargo event: {event_type} at {visit['port_name']} "
                            f"by {vessel.name} (MMSI={mmsi}), "
                            f"Δdraft={draft_change:.1f}m, ~{volume:.0f} barrels"
                        )

                # Clear the visit
                del _active_port_visits[mmsi]
