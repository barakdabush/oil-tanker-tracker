"""Chokepoint monitoring engine.

Tracks tanker transits through critical maritime chokepoints (Hormuz, Suez,
Malacca, etc.) using a hybrid detection algorithm:
  1. Live polygon containment (entry/exit tracking for vessels broadcasting AIS
     inside the chokepoint boundary polygon).
  2. Gate-line & polygon-segment crossing detection across consecutive vessel
     position pairs (catches vessels that turn off AIS / go dark before a
     strait and reappear on the other side, or jump across narrow straits
     between sparse AIS pings).
"""

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from shapely import wkt as shapely_wkt
from shapely.geometry import LineString, Point, Polygon
from sqlalchemy import and_, select, text

from app.config import settings
from app.database import async_session
from app.models import ChokepointTransit, Vessel
from app.utils.geo import (
    bearing_to_direction,
    haversine_km,
    initial_bearing_deg,
    segment_crosses_linestring,
    segment_intersects_polygon,
)

logger = logging.getLogger(__name__)

# Fallback gate lines (lon, lat) in case a chokepoint row has gate_line IS NULL.
# Each transect crosses the strait throat and extends inland across adjacent land
# barriers so straight-line chords between pre-gap and post-gap positions
# reliably intersect the gate even around coastal bends (e.g. Musandam Peninsula).
DEFAULT_GATE_LINES_WKT: dict[str, str] = {
    "Strait of Hormuz": "LINESTRING(56.45 27.35, 56.35 26.40, 55.90 24.50)",
    "Strait of Malacca": "LINESTRING(101.00 0.50, 102.80 3.00)",
    "Suez Canal": "LINESTRING(31.50 30.55, 33.30 30.55)",
    "Bab el-Mandeb": "LINESTRING(42.40 12.20, 44.10 13.30)",
    "Turkish Straits (Bosporus)": "LINESTRING(28.40 41.12, 29.80 41.12)",
    "Danish Straits": "LINESTRING(9.80 55.40, 13.50 55.80)",
    "Cape of Good Hope": "LINESTRING(18.50 -33.80, 18.50 -37.50)",
    "Panama Canal": "LINESTRING(-80.30 8.90, -79.20 9.35)",
}

# Track which vessels are currently inside each chokepoint
# Key: (mmsi, chokepoint_id), Value: transit_start_time
_vessels_in_chokepoint: dict[tuple[int, int], datetime] = {}


@dataclass(frozen=True)
class ChokepointGeometry:
    """In-memory Shapely geometry representation of a chokepoint."""

    id: int
    name: str
    congestion_threshold: int
    polygon: Optional[Polygon]
    gate_line: Optional[LineString]


@dataclass(frozen=True)
class TrackPoint:
    """Single chronological vessel position point."""

    time: datetime
    lat: float
    lon: float
    course: Optional[float] = None
    draft: Optional[float] = None


@dataclass(frozen=True)
class CrossingMatch:
    """Result of a detected segment crossing through a chokepoint."""

    crossing_time: datetime
    direction: str
    method: str  # "gate_crossing" | "polygon_segment"
    fraction: float
    p1: TrackPoint
    p2: TrackPoint


@dataclass
class TrackEvaluationResult:
    """Transit events produced by evaluating a vessel track against a chokepoint."""

    new_transits: list[dict]
    closed_transits: list[dict]
    direction_updates: list[dict]


def detect_segment_crossing(
    p1: TrackPoint,
    p2: TrackPoint,
    cp: ChokepointGeometry,
    *,
    max_gap_hours: float = 120.0,
    max_distance_km: float = 1500.0,
    max_implied_speed_knots: float = 35.0,
) -> Optional[CrossingMatch]:
    """Determine whether the movement segment (p1 -> p2) crossed chokepoint `cp`.

    Applies temporal, spatial, and physical-speed safeguards to reject GPS
    spoofing / teleport glitches and stale cross-ocean gaps.
    """
    dt_seconds = (p2.time - p1.time).total_seconds()
    if dt_seconds <= 0:
        return None

    dt_hours = dt_seconds / 3600.0
    if dt_hours > max_gap_hours:
        return None

    dist_km = haversine_km(p1.lat, p1.lon, p2.lat, p2.lon)
    if dist_km < 0.05 or dist_km > max_distance_km:
        return None

    # Enforce physical speed cap on intervals >= 3 minutes (avoids noise on tiny deltas)
    if dt_hours >= 0.05:
        implied_knots = (dist_km / 1.852) / dt_hours
        if implied_knots > max_implied_speed_knots:
            return None

    # Primary check: gate-line crossing (handles dark transits & coastal bends)
    if cp.gate_line is not None:
        frac = segment_crosses_linestring(p1.lon, p1.lat, p2.lon, p2.lat, cp.gate_line)
        if frac is not None:
            crossing_time = p1.time + timedelta(seconds=frac * dt_seconds)
            direction = bearing_to_direction(
                initial_bearing_deg(p1.lat, p1.lon, p2.lat, p2.lon)
            )
            return CrossingMatch(
                crossing_time=crossing_time,
                direction=direction,
                method="gate_crossing",
                fraction=frac,
                p1=p1,
                p2=p2,
            )

    # Secondary fallback: segment passes completely across boundary_polygon
    # without either endpoint being inside (e.g. narrow strait / no gate line)
    if cp.polygon is not None:
        p1_inside = cp.polygon.contains(Point(p1.lon, p1.lat))
        p2_inside = cp.polygon.contains(Point(p2.lon, p2.lat))
        if not p1_inside and not p2_inside:
            frac = segment_intersects_polygon(p1.lon, p1.lat, p2.lon, p2.lat, cp.polygon)
            if frac is not None:
                crossing_time = p1.time + timedelta(seconds=frac * dt_seconds)
                direction = bearing_to_direction(
                    initial_bearing_deg(p1.lat, p1.lon, p2.lat, p2.lon)
                )
                return CrossingMatch(
                    crossing_time=crossing_time,
                    direction=direction,
                    method="polygon_segment",
                    fraction=frac,
                    p1=p1,
                    p2=p2,
                )

    return None


def evaluate_vessel_track(
    *,
    mmsi: int,
    vessel_draft: Optional[float],
    vessel_course: Optional[float],
    track: list[TrackPoint],
    cp: ChokepointGeometry,
    active_transits: dict[tuple[int, int], datetime],
    recent_transit_times: dict[tuple[int, int], datetime],
    now: datetime,
    scan_since: datetime,
    cooldown_hours: float = 12.0,
    max_gap_hours: float = 120.0,
    max_distance_km: float = 1500.0,
    max_implied_speed_knots: float = 35.0,
) -> TrackEvaluationResult:
    """Evaluate a vessel's chronological track against a single chokepoint.

    Combines segment-crossing detection (for dark/sparse AIS) with live polygon
    entry/exit tracking, while preventing duplicate transits via active-state
    tracking and a per-(mmsi, chokepoint) cooldown window.
    """
    result = TrackEvaluationResult(new_transits=[], closed_transits=[], direction_updates=[])
    if not track:
        return result

    key = (mmsi, cp.id)
    cooldown_delta = timedelta(hours=cooldown_hours)

    def _in_cooldown(event_time: datetime) -> bool:
        last_t = recent_transit_times.get(key)
        if last_t is None:
            return False
        return abs((event_time - last_t).total_seconds()) < cooldown_delta.total_seconds()

    # Step 1: Check consecutive position segments that arrived within the scan window
    for idx in range(len(track) - 1):
        p1 = track[idx]
        p2 = track[idx + 1]
        if p2.time < scan_since:
            continue

        match = detect_segment_crossing(
            p1,
            p2,
            cp,
            max_gap_hours=max_gap_hours,
            max_distance_km=max_distance_km,
            max_implied_speed_knots=max_implied_speed_knots,
        )
        if match is None:
            continue

        if key in active_transits:
            # Vessel already has an open polygon transit — confirm/refine direction
            result.direction_updates.append(
                {
                    "mmsi": mmsi,
                    "chokepoint_id": cp.id,
                    "direction": match.direction,
                }
            )
            continue

        if _in_cooldown(match.crossing_time):
            continue

        # If this is the final point and the vessel is currently inside the
        # chokepoint polygon, open the transit so its eventual exit is tracked.
        is_final_point = idx + 1 == len(track) - 1
        p2_inside = (
            is_final_point
            and cp.polygon is not None
            and cp.polygon.contains(Point(p2.lon, p2.lat))
        )

        if p2_inside:
            active_transits[key] = match.crossing_time
            exit_time = None
        else:
            exit_time = match.crossing_time

        recent_transit_times[key] = match.crossing_time
        result.new_transits.append(
            {
                "time": match.crossing_time,
                "chokepoint_id": cp.id,
                "mmsi": mmsi,
                "entry_time": match.crossing_time,
                "exit_time": exit_time,
                "direction": match.direction,
                "transit_duration_min": None,
                "draft_on_entry": p2.draft if p2.draft is not None else vessel_draft,
                "detection_method": match.method,
            }
        )

    # Step 2: Live polygon containment check on the vessel's latest position
    latest = track[-1]
    is_inside = cp.polygon is not None and cp.polygon.contains(Point(latest.lon, latest.lat))

    if is_inside and key not in active_transits:
        if not _in_cooldown(now):
            active_transits[key] = now
            recent_transit_times[key] = now
            course = latest.course if latest.course is not None else vessel_course
            direction = bearing_to_direction(course)
            result.new_transits.append(
                {
                    "time": now,
                    "chokepoint_id": cp.id,
                    "mmsi": mmsi,
                    "entry_time": now,
                    "exit_time": None,
                    "direction": direction,
                    "transit_duration_min": None,
                    "draft_on_entry": latest.draft if latest.draft is not None else vessel_draft,
                    "detection_method": "polygon",
                }
            )
    elif not is_inside and key in active_transits:
        entry_time = active_transits.pop(key)
        transit_duration = max(0.0, (now - entry_time).total_seconds() / 60.0)
        recent_transit_times[key] = now
        result.closed_transits.append(
            {
                "mmsi": mmsi,
                "chokepoint_id": cp.id,
                "exit_time": now,
                "transit_duration_min": round(transit_duration, 1),
            }
        )

    return result


class ChokepointMonitor:
    """Monitors tanker traffic through maritime chokepoints."""

    def __init__(self) -> None:
        self._hydrated: bool = False
        self._last_scan_time: Optional[datetime] = None

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

    async def _hydrate_active_transits(self, session) -> None:
        """Load open transits (exit_time IS NULL) from DB on first run after startup."""
        if self._hydrated:
            return
        open_q = select(
            ChokepointTransit.mmsi,
            ChokepointTransit.chokepoint_id,
            ChokepointTransit.entry_time,
        ).where(ChokepointTransit.exit_time.is_(None))
        rows = (await session.execute(open_q)).all()
        for row in rows:
            key = (row.mmsi, row.chokepoint_id)
            if key not in _vessels_in_chokepoint:
                _vessels_in_chokepoint[key] = row.entry_time
        self._hydrated = True
        if rows:
            logger.info(f"Hydrated {len(rows)} active chokepoint transits from database.")

    @staticmethod
    async def _load_chokepoints(session) -> list[ChokepointGeometry]:
        """Load all chokepoints with their Shapely polygon and gate_line geometries."""
        rows = (
            await session.execute(
                text(
                    "SELECT id, name, congestion_threshold, "
                    "ST_AsText(boundary_polygon) AS poly_wkt, "
                    "ST_AsText(gate_line) AS gate_wkt "
                    "FROM chokepoints"
                )
            )
        ).all()

        geoms: list[ChokepointGeometry] = []
        for row in rows:
            polygon = shapely_wkt.loads(row.poly_wkt) if row.poly_wkt else None
            gate_wkt = row.gate_wkt or DEFAULT_GATE_LINES_WKT.get(row.name)
            gate_line = shapely_wkt.loads(gate_wkt) if gate_wkt else None
            geoms.append(
                ChokepointGeometry(
                    id=row.id,
                    name=row.name,
                    congestion_threshold=row.congestion_threshold or 20,
                    polygon=polygon,
                    gate_line=gate_line,
                )
            )
        return geoms

    async def monitor(self):
        """Check tanker tracks and positions against chokepoint gate lines and polygons."""
        start_time = time.time()
        stats = {
            "entries": 0,
            "gate_crossings": 0,
            "exits": 0,
            "congestion": 0,
            "low": 0,
        }

        now = datetime.now(timezone.utc)
        if self._last_scan_time is not None:
            # Overlap slightly with previous scan to never drop boundary-timestamp pings
            scan_since = min(
                now - timedelta(minutes=30),
                self._last_scan_time - timedelta(minutes=2),
            )
            # Clamp to 6 hours if worker was paused for a long time
            scan_since = max(scan_since, now - timedelta(hours=6))
        else:
            scan_since = now - timedelta(minutes=30)

        max_gap_hours = float(settings.CHOKEPOINT_MAX_CROSSING_GAP_HOURS)
        max_distance_km = float(settings.CHOKEPOINT_MAX_CROSSING_DISTANCE_KM)
        max_implied_speed_knots = float(settings.CHOKEPOINT_MAX_IMPLIED_SPEED_KNOTS)
        cooldown_hours = float(settings.CHOKEPOINT_TRANSIT_COOLDOWN_HOURS)

        gap_cutoff = scan_since - timedelta(hours=max_gap_hours)
        cooldown_cutoff = now - timedelta(hours=cooldown_hours + max_gap_hours)

        async with async_session() as session:
            await self._hydrate_active_transits(session)

            chokepoints = await self._load_chokepoints(session)
            if not chokepoints:
                self._last_scan_time = now
                return

            # Get tanker vessels with recent positions
            vessels_q = select(Vessel).where(
                and_(
                    Vessel.ship_type.in_(settings.AIS_TANKER_TYPES),
                    Vessel.last_seen >= scan_since,
                    Vessel.last_lat.isnot(None),
                    Vessel.last_lon.isnot(None),
                )
            )
            vessels = (await session.execute(vessels_q)).scalars().all()
            if not vessels:
                self._last_scan_time = now
                return

            mmsis = [v.mmsi for v in vessels]

            # Batch-fetch recent positions inside scan window
            recent_pos_rows = (
                await session.execute(
                    text("""
                        SELECT mmsi, time, latitude, longitude, course, draft
                        FROM vessel_positions
                        WHERE mmsi = ANY(:mmsis) AND time >= :scan_since
                        ORDER BY mmsi, time ASC
                    """),
                    {"mmsis": mmsis, "scan_since": scan_since},
                )
            ).all()

            # Batch-fetch the single anchor position right before scan_since
            # (within max_gap_hours) for each vessel so dark-AIS gaps are bridged.
            anchor_pos_rows = (
                await session.execute(
                    text("""
                        SELECT DISTINCT ON (mmsi)
                            mmsi, time, latitude, longitude, course, draft
                        FROM vessel_positions
                        WHERE mmsi = ANY(:mmsis)
                          AND time >= :gap_cutoff
                          AND time < :scan_since
                        ORDER BY mmsi, time DESC
                    """),
                    {"mmsis": mmsis, "gap_cutoff": gap_cutoff, "scan_since": scan_since},
                )
            ).all()

            # Build per-vessel chronological tracks
            tracks: dict[int, list[TrackPoint]] = defaultdict(list)
            for row in anchor_pos_rows:
                tracks[row.mmsi].append(
                    TrackPoint(
                        time=row.time,
                        lat=row.latitude,
                        lon=row.longitude,
                        course=row.course,
                        draft=row.draft,
                    )
                )
            for row in recent_pos_rows:
                tracks[row.mmsi].append(
                    TrackPoint(
                        time=row.time,
                        lat=row.latitude,
                        lon=row.longitude,
                        course=row.course,
                        draft=row.draft,
                    )
                )

            # Ensure each vessel's latest position from `vessels` is included
            for vessel in vessels:
                v_track = tracks[vessel.mmsi]
                if vessel.last_seen is not None and (
                    not v_track or vessel.last_seen > v_track[-1].time
                ):
                    v_track.append(
                        TrackPoint(
                            time=vessel.last_seen,
                            lat=vessel.last_lat,
                            lon=vessel.last_lon,
                            course=vessel.last_course,
                            draft=vessel.draft,
                        )
                    )

            # Batch-fetch most recent transit timestamp per (mmsi, chokepoint_id) for cooldown
            recent_transit_rows = (
                await session.execute(
                    text("""
                        SELECT mmsi, chokepoint_id,
                               MAX(COALESCE(exit_time, entry_time)) AS last_transit_time
                        FROM chokepoint_transits
                        WHERE mmsi = ANY(:mmsis) AND entry_time >= :cooldown_cutoff
                        GROUP BY mmsi, chokepoint_id
                    """),
                    {"mmsis": mmsis, "cooldown_cutoff": cooldown_cutoff},
                )
            ).all()
            recent_transit_times: dict[tuple[int, int], datetime] = {
                (r.mmsi, r.chokepoint_id): r.last_transit_time
                for r in recent_transit_rows
                if r.last_transit_time is not None
            }

            # Evaluate each vessel against each chokepoint in memory (Shapely)
            for vessel in vessels:
                v_track = tracks.get(vessel.mmsi)
                if not v_track:
                    continue

                for cp in chokepoints:
                    eval_res = evaluate_vessel_track(
                        mmsi=vessel.mmsi,
                        vessel_draft=vessel.draft,
                        vessel_course=vessel.last_course,
                        track=v_track,
                        cp=cp,
                        active_transits=_vessels_in_chokepoint,
                        recent_transit_times=recent_transit_times,
                        now=now,
                        scan_since=scan_since,
                        cooldown_hours=cooldown_hours,
                        max_gap_hours=max_gap_hours,
                        max_distance_km=max_distance_km,
                        max_implied_speed_knots=max_implied_speed_knots,
                    )

                    for nt in eval_res.new_transits:
                        session.add(ChokepointTransit(**nt))
                        stats["entries"] += 1
                        if nt["detection_method"] != "polygon":
                            stats["gate_crossings"] += 1
                        logger.debug(
                            f"Chokepoint TRANSIT ({nt['detection_method']}): "
                            f"{vessel.name} ({vessel.mmsi}) -> {cp.name} "
                            f"({nt['direction']}), draft={nt['draft_on_entry']}"
                        )

                    for upd in eval_res.direction_updates:
                        transit_q = (
                            select(ChokepointTransit)
                            .where(
                                and_(
                                    ChokepointTransit.mmsi == upd["mmsi"],
                                    ChokepointTransit.chokepoint_id == upd["chokepoint_id"],
                                    ChokepointTransit.exit_time.is_(None),
                                )
                            )
                            .order_by(ChokepointTransit.entry_time.desc())
                            .limit(1)
                        )
                        transit = (await session.execute(transit_q)).scalar_one_or_none()
                        if transit and (not transit.direction or transit.direction == "unknown"):
                            transit.direction = upd["direction"]

                    for ct in eval_res.closed_transits:
                        transit_q = (
                            select(ChokepointTransit)
                            .where(
                                and_(
                                    ChokepointTransit.mmsi == ct["mmsi"],
                                    ChokepointTransit.chokepoint_id == ct["chokepoint_id"],
                                    ChokepointTransit.exit_time.is_(None),
                                )
                            )
                            .order_by(ChokepointTransit.entry_time.desc())
                            .limit(1)
                        )
                        transit = (await session.execute(transit_q)).scalar_one_or_none()
                        if transit:
                            transit.exit_time = ct["exit_time"]
                            transit.transit_duration_min = ct["transit_duration_min"]
                        stats["exits"] += 1
                        logger.debug(
                            f"Chokepoint EXIT: {vessel.name} ({vessel.mmsi}) exited {cp.name} "
                            f"after {ct['transit_duration_min']:.0f} min"
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

        self._last_scan_time = now
        duration = time.time() - start_time
        if any(stats.values()):
            logger.info(
                f"Chokepoint scan complete in {duration:.1f}s — "
                f"Active Transits: {len(_vessels_in_chokepoint)}. "
                f"New Entries: {stats['entries']} (Gate Crossings: {stats['gate_crossings']}), "
                f"New Exits: {stats['exits']}, "
                f"Congestion Warns: {stats['congestion']}, Low Warns: {stats['low']}"
            )
        else:
            logger.info(
                f"Chokepoint scan complete in {duration:.1f}s — "
                f"No new events. Active Transits: {len(_vessels_in_chokepoint)}"
            )

    @staticmethod
    def _determine_direction(course: float | None) -> str:
        """Determine compass direction from course angle."""
        return bearing_to_direction(course)
