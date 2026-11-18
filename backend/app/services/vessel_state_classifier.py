"""Vessel operational state classifier and interval history recorder.

Periodically classifies every recently-seen tanker into a single mutually-exclusive
operational state (dark, sts_transfer, at_port, anchored_waiting, floating_storage,
in_transit_laden, in_transit_ballast, idle_drifting), records state-transition
intervals in `vessel_state_history`, and rolls up daily port congestion metrics
into `port_daily_stats`.
"""

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import and_, desc, func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.database import async_session
from app.models import (
    AISGap,
    CargoEvent,
    Port,
    PortDailyStat,
    STSEvent,
    Vessel,
    VesselPosition,
    VesselStateHistory,
)
from app.utils.geo import haversine_km

logger = logging.getLogger(__name__)

CLASSIFY_INTERVAL_SECONDS = 5 * 60  # 5 minutes
DARK_AGE_HOURS = 6.0
AT_PORT_RADIUS_KM = 5.0
ANCHORAGE_RADIUS_KM = 25.0
ANCHOR_NAV_MAX_PORT_KM = 40.0
FLOATING_STORAGE_MIN_DAYS = 7.0


def determine_cargo_status(
    draft: Optional[float],
    min_draft: Optional[float],
    max_draft: Optional[float],
    latest_cargo_event_type: Optional[str] = None,
) -> str:
    """Infer whether a tanker is laden or ballast from its draft bounds or cargo events."""
    if (
        draft is not None
        and min_draft is not None
        and max_draft is not None
        and (max_draft - min_draft) >= 0.8
    ):
        ratio = (draft - min_draft) / (max_draft - min_draft)
        if ratio >= 0.6:
            return "laden"
        if ratio <= 0.4:
            return "ballast"

    if latest_cargo_event_type == "loading":
        return "laden"
    if latest_cargo_event_type == "unloading":
        return "ballast"
    return "unknown"


def classify_vessel_state(
    *,
    age_hours: float,
    speed: Optional[float],
    nav_status: Optional[int],
    cargo_status: str,
    is_dark: bool,
    in_active_sts: bool,
    nearest_port_id: Optional[int],
    nearest_port_dist_km: Optional[float],
    stationary_days: float = 0.0,
) -> tuple[str, Optional[int]]:
    """Return (operational_state, current_port_id) using mutually-exclusive priority rules."""
    if is_dark or age_hours > DARK_AGE_HOURS:
        return "dark", None

    if in_active_sts:
        return "sts_transfer", None

    spd = speed if speed is not None else 0.0

    # 3. Berthed / moored at port
    if (
        nearest_port_id is not None
        and nearest_port_dist_km is not None
        and nearest_port_dist_km <= AT_PORT_RADIUS_KM
        and (nav_status == 5 or spd <= 0.5)
    ):
        return "at_port", nearest_port_id

    # 4. Anchored waiting outside port (port queue / congestion)
    if nearest_port_id is not None and nearest_port_dist_km is not None and spd <= 1.5:
        if nearest_port_dist_km <= ANCHORAGE_RADIUS_KM or (
            nav_status == 1 and nearest_port_dist_km <= ANCHOR_NAV_MAX_PORT_KM
        ):
            return "anchored_waiting", nearest_port_id

    # 5. Floating storage (laden, stationary offshore >= 7 days)
    if (
        spd <= 1.5
        and (nearest_port_dist_km is None or nearest_port_dist_km > ANCHORAGE_RADIUS_KM)
        and cargo_status == "laden"
        and stationary_days >= FLOATING_STORAGE_MIN_DAYS
    ):
        return "floating_storage", None

    # 6 & 7. Underway in transit
    if spd > 3.0 or nav_status == 0 and spd > 1.5:
        if cargo_status == "laden":
            return "in_transit_laden", None
        return "in_transit_ballast", None

    # 8. Idle / drifting offshore
    return "idle_drifting", None


class VesselStateClassifier:
    """Classifies active tankers into operational states and logs transitions."""

    async def run_periodic(self):
        """Run vessel state classification every 5 minutes."""
        await asyncio.sleep(20)
        while True:
            try:
                await self.classify_all()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Vessel state classification error: {e}")
            await asyncio.sleep(CLASSIFY_INTERVAL_SECONDS)

    async def classify_all(self) -> dict[str, int]:
        """Classify all recently-seen vessels and update port daily stats."""
        start_time = time.time()
        now = datetime.now(timezone.utc)
        recency_cutoff = now - timedelta(hours=48)
        transitions = 0

        async with async_session() as session:
            # 1. Load ports for fast in-memory proximity lookup
            ports = (
                await session.execute(
                    select(Port.id, Port.latitude, Port.longitude)
                )
            ).all()

            # 2. Load recently seen vessels
            vessels = (
                await session.execute(
                    select(Vessel).where(
                        Vessel.last_seen >= recency_cutoff,
                        Vessel.last_lat.isnot(None),
                        Vessel.last_lon.isnot(None),
                    )
                )
            ).scalars().all()

            if not vessels:
                return {"classified": 0, "transitions": 0}

            mmsis = [v.mmsi for v in vessels]

            # 3. Open dark gaps
            dark_mmsis = set(
                (
                    await session.execute(
                        select(AISGap.mmsi).where(
                            AISGap.mmsi.in_(mmsis),
                            AISGap.status.in_(["dark", "extended_dark"]),
                        )
                    )
                ).scalars().all()
            )

            # 4. Active STS encounters in the last 6h
            sts_cutoff = now - timedelta(hours=6)
            sts_rows = (
                await session.execute(
                    select(STSEvent.mmsi_1, STSEvent.mmsi_2).where(
                        or_cond := and_(
                            STSEvent.start_time >= sts_cutoff,
                            STSEvent.end_time.is_(None),
                        )
                    )
                )
            ).all()
            sts_mmsis = {m for row in sts_rows for m in (row.mmsi_1, row.mmsi_2)}

            # 5. Latest nav_status per MMSI (last 6h)
            nav_rows = (
                await session.execute(
                    text("""
                        SELECT DISTINCT ON (mmsi) mmsi, nav_status
                        FROM vessel_positions
                        WHERE time >= :cutoff AND nav_status IS NOT NULL
                        ORDER BY mmsi, time DESC
                    """),
                    {"cutoff": now - timedelta(hours=6)},
                )
            ).all()
            nav_by_mmsi = {r.mmsi: r.nav_status for r in nav_rows}

            # 6. Latest cargo event per MMSI (last 30d)
            cargo_rows = (
                await session.execute(
                    text("""
                        SELECT DISTINCT ON (mmsi) mmsi, event_type
                        FROM cargo_events
                        WHERE created_at >= :cutoff
                        ORDER BY mmsi, created_at DESC
                    """),
                    {"cutoff": now - timedelta(days=30)},
                )
            ).all()
            cargo_by_mmsi = {r.mmsi: r.event_type for r in cargo_rows}

            # 7. Classify each vessel
            port_live_counts: dict[int, dict[str, list[float]]] = {}
            for v in vessels:
                # Update observed draft bounds
                if v.draft is not None and 1.0 < v.draft < 35.0:
                    if v.min_draft is None or v.draft < v.min_draft:
                        v.min_draft = v.draft
                    if v.max_draft is None or v.draft > v.max_draft:
                        v.max_draft = v.draft

                cargo_status = determine_cargo_status(
                    v.draft, v.min_draft, v.max_draft, cargo_by_mmsi.get(v.mmsi)
                )
                v.cargo_status = cargo_status

                # Find nearest port within ~0.4 deg (~44 km)
                nearest_port_id = None
                nearest_dist_km = None
                for pid, plat, plon in ports:
                    if abs(v.last_lat - plat) > 0.45 or abs(v.last_lon - plon) > 0.55:
                        continue
                    d_km = haversine_km(v.last_lat, v.last_lon, plat, plon)
                    if nearest_dist_km is None or d_km < nearest_dist_km:
                        nearest_dist_km = d_km
                        nearest_port_id = pid

                age_hours = (now - v.last_seen).total_seconds() / 3600.0 if v.last_seen else 999.0
                stationary_days = 0.0
                if v.operational_state in ("idle_drifting", "floating_storage") and v.state_since:
                    stationary_days = (now - v.state_since).total_seconds() / 86400.0

                new_state, new_port_id = classify_vessel_state(
                    age_hours=age_hours,
                    speed=v.last_speed,
                    nav_status=nav_by_mmsi.get(v.mmsi),
                    cargo_status=cargo_status,
                    is_dark=v.mmsi in dark_mmsis,
                    in_active_sts=v.mmsi in sts_mmsis,
                    nearest_port_id=nearest_port_id,
                    nearest_port_dist_km=nearest_dist_km,
                    stationary_days=stationary_days,
                )

                if new_state != v.operational_state or new_port_id != v.current_port_id:
                    # Close open interval in vessel_state_history
                    open_rows = (
                        await session.execute(
                            select(VesselStateHistory).where(
                                VesselStateHistory.mmsi == v.mmsi,
                                VesselStateHistory.ended_at.is_(None),
                            )
                        )
                    ).scalars().all()
                    for open_row in open_rows:
                        open_row.ended_at = now
                        open_row.duration_hours = round(
                            max(0.0, (now - open_row.started_at).total_seconds() / 3600.0), 2
                        )

                    # Open new interval
                    session.add(
                        VesselStateHistory(
                            mmsi=v.mmsi,
                            state=new_state,
                            cargo_status=cargo_status,
                            port_id=new_port_id,
                            started_at=now,
                            ended_at=None,
                            duration_hours=None,
                            start_lat=v.last_lat,
                            start_lon=v.last_lon,
                        )
                    )
                    v.operational_state = new_state
                    v.state_since = now
                    v.current_port_id = new_port_id
                    transitions += 1

                # Accumulate live port congestion stats
                if new_port_id is not None and new_state in ("at_port", "anchored_waiting"):
                    bucket = port_live_counts.setdefault(
                        new_port_id, {"berthed": [], "anchored": []}
                    )
                    wait_h = (
                        max(0.0, (now - v.state_since).total_seconds() / 3600.0)
                        if v.state_since
                        else 0.0
                    )
                    if new_state == "at_port":
                        bucket["berthed"].append(wait_h)
                    else:
                        bucket["anchored"].append(wait_h)

            # 8. Upsert today's port_daily_stats
            today = now.date()
            if port_live_counts:
                # Fetch 90-day peak per port for utilization_pct
                peak_rows = (
                    await session.execute(
                        select(
                            PortDailyStat.port_id,
                            func.max(PortDailyStat.berthed_count + PortDailyStat.anchored_waiting_count).label("peak"),
                        )
                        .where(PortDailyStat.stat_date >= today - timedelta(days=90))
                        .group_by(PortDailyStat.port_id)
                    )
                ).all()
                peaks = {r.port_id: int(r.peak or 0) for r in peak_rows}

                for pid, counts in port_live_counts.items():
                    berthed = len(counts["berthed"])
                    anchored = len(counts["anchored"])
                    total_at_port = berthed + anchored
                    peak = max(peaks.get(pid, 0), total_at_port, 1)
                    util_pct = round(min(100.0, (total_at_port / peak) * 100.0), 1)
                    avg_wait = (
                        round(sum(counts["anchored"]) / anchored, 2) if anchored > 0 else 0.0
                    )

                    stmt = pg_insert(PortDailyStat).values(
                        port_id=pid,
                        stat_date=today,
                        berthed_count=berthed,
                        anchored_waiting_count=anchored,
                        avg_wait_hours=avg_wait,
                        utilization_pct=util_pct,
                        updated_at=now,
                    )
                    stmt = stmt.on_conflict_do_update(
                        index_elements=["port_id", "stat_date"],
                        set_={
                            "berthed_count": stmt.excluded.berthed_count,
                            "anchored_waiting_count": stmt.excluded.anchored_waiting_count,
                            "avg_wait_hours": stmt.excluded.avg_wait_hours,
                            "utilization_pct": stmt.excluded.utilization_pct,
                            "updated_at": stmt.excluded.updated_at,
                        },
                    )
                    await session.execute(stmt)

            await session.commit()

        elapsed = round(time.time() - start_time, 2)
        if transitions > 0:
            logger.info(
                f"VesselStateClassifier: classified {len(vessels)} vessels, "
                f"{transitions} transitions across {len(port_live_counts)} ports in {elapsed}s"
            )
        return {"classified": len(vessels), "transitions": transitions}
