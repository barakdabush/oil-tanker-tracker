"""Geographic utility functions."""

import math
from datetime import datetime
from typing import Callable, Optional, TypeVar

from shapely.geometry import LineString, MultiPoint, Point, Polygon

EARTH_RADIUS_KM = 6371.0
NAUTICAL_MILE_KM = 1.852
MAX_PLAUSIBLE_TANKER_SPEED_KNOTS = 40.0
POSITION_JITTER_GRACE_KM = 5.0
NULL_ISLAND_EPSILON_DEG = 0.001

T = TypeVar("T")


def is_valid_coordinate(lat: Optional[float], lon: Optional[float]) -> bool:
    """Return True if (lat, lon) is a finite, in-range, non-Null-Island coordinate."""
    if lat is None or lon is None:
        return False
    if isinstance(lat, bool) or isinstance(lon, bool):
        return False
    if not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        return False
    if math.isnan(lat) or math.isinf(lat) or math.isnan(lon) or math.isinf(lon):
        return False
    if not (-90.0 <= lat <= 90.0) or not (-180.0 <= lon <= 180.0):
        return False
    if abs(lat) < NULL_ISLAND_EPSILON_DEG and abs(lon) < NULL_ISLAND_EPSILON_DEG:
        return False
    return True


def is_plausible_move(
    prev_lat: float,
    prev_lon: float,
    prev_time: datetime,
    lat: float,
    lon: float,
    timestamp: datetime,
    *,
    max_speed_knots: float = MAX_PLAUSIBLE_TANKER_SPEED_KNOTS,
    jitter_km: float = POSITION_JITTER_GRACE_KM,
) -> bool:
    """Check whether moving from (prev_lat, prev_lon, prev_time) to (lat, lon, timestamp) is physically plausible."""
    dist_km = haversine_km(prev_lat, prev_lon, lat, lon)
    if dist_km <= jitter_km:
        return True
    dt_seconds = (timestamp - prev_time).total_seconds()
    if dt_seconds <= 0:
        return False
    dt_hours = max(dt_seconds, 1.0) / 3600.0
    implied_knots = (dist_km / NAUTICAL_MILE_KM) / dt_hours
    return implied_knots <= max_speed_knots


def despike_positions(
    positions: list[T],
    *,
    get_lat: Callable[[T], Optional[float]] = lambda p: p.latitude,
    get_lon: Callable[[T], Optional[float]] = lambda p: p.longitude,
    get_time: Callable[[T], datetime] = lambda p: p.time,
    max_speed_knots: float = MAX_PLAUSIBLE_TANKER_SPEED_KNOTS,
    jitter_km: float = POSITION_JITTER_GRACE_KM,
    quorum: int = 3,
) -> tuple[list[T], list[T]]:
    """Filter invalid coordinates and physically impossible teleport spikes from a chronological track.

    Returns ``(clean_positions, rejected_positions)``.
    Uses a small confirmation quorum so:
    - Isolated 1–2 point GPS-spoofing / NMEA-corruption spikes (start, middle, or end) are rejected.
    - A corrupted initial point is replaced once >=2 consistent points establish the real track.
    - A genuine relocation after an unrecorded gap or prior corrupted anchor recovers once ``quorum``
      consecutive consistent reports agree on the new position.
    """
    accepted: list[T] = []
    rejected: list[T] = []
    pending: list[T] = []

    for pt in positions:
        lat = get_lat(pt)
        lon = get_lon(pt)
        if not is_valid_coordinate(lat, lon):
            rejected.append(pt)
            continue

        ts = get_time(pt)
        if not accepted:
            accepted.append(pt)
            continue

        last = accepted[-1]
        if is_plausible_move(
            get_lat(last),  # type: ignore[arg-type]
            get_lon(last),  # type: ignore[arg-type]
            get_time(last),
            lat,  # type: ignore[arg-type]
            lon,  # type: ignore[arg-type]
            ts,
            max_speed_knots=max_speed_knots,
            jitter_km=jitter_km,
        ):
            if pending:
                rejected.extend(pending)
                pending = []
            accepted.append(pt)
            continue

        # Implied speed from last trusted point is impossible — check if consistent with pending cluster
        if pending and is_plausible_move(
            get_lat(pending[-1]),  # type: ignore[arg-type]
            get_lon(pending[-1]),  # type: ignore[arg-type]
            get_time(pending[-1]),
            lat,  # type: ignore[arg-type]
            lon,  # type: ignore[arg-type]
            ts,
            max_speed_knots=max_speed_knots,
            jitter_km=jitter_km,
        ):
            pending.append(pt)
        else:
            if pending:
                rejected.extend(pending)
            pending = [pt]

        if len(accepted) < quorum and len(pending) > len(accepted):
            # Initial point(s) were the outlier; replace with the consistent cluster
            rejected.extend(accepted)
            accepted = pending
            pending = []
        elif len(pending) >= quorum:
            # Quorum reached: vessel genuinely relocated or recovered from corrupted history
            accepted.extend(pending)
            pending = []

    if pending:
        rejected.extend(pending)

    return accepted, rejected


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the great circle distance in kilometers between two points."""
    lat1, lon1, lat2, lon2 = map(math.radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def initial_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate the initial compass bearing (0-360 deg, 0=North) from point 1 to point 2."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(phi2)
    y = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def bearing_to_direction(course: Optional[float]) -> str:
    """Convert a compass bearing/course angle (0-360 deg) to a cardinal direction label."""
    if course is None:
        return "unknown"
    course = course % 360.0
    if 315 <= course or course < 45:
        return "northbound"
    if 45 <= course < 135:
        return "eastbound"
    if 135 <= course < 225:
        return "southbound"
    return "westbound"


def _intersection_fraction(segment: LineString, geom) -> Optional[float]:
    """Return normalized distance [0.0, 1.0] along `segment` where it intersects `geom`."""
    if segment.length == 0.0:
        return None
    if not segment.intersects(geom):
        return None

    inter = segment.intersection(geom)
    if inter.is_empty:
        return None

    if isinstance(inter, Point):
        frac = segment.project(inter, normalized=True)
    elif isinstance(inter, MultiPoint):
        frac = min(segment.project(pt, normalized=True) for pt in inter.geoms)
    else:
        # Overlapping LineString / GeometryCollection — use representative midpoint
        frac = segment.project(inter.centroid, normalized=True)

    return max(0.0, min(1.0, float(frac)))


def segment_crosses_linestring(
    p1_lon: float,
    p1_lat: float,
    p2_lon: float,
    p2_lat: float,
    gate_line: LineString,
) -> Optional[float]:
    """Check if the segment (p1 -> p2) crosses `gate_line`.

    Returns the normalized intersection fraction in [0.0, 1.0] along (p1 -> p2),
    or None if the segment does not intersect the gate line.
    """
    if p1_lon == p2_lon and p1_lat == p2_lat:
        return None
    segment = LineString([(p1_lon, p1_lat), (p2_lon, p2_lat)])
    return _intersection_fraction(segment, gate_line)


def segment_intersects_polygon(
    p1_lon: float,
    p1_lat: float,
    p2_lon: float,
    p2_lat: float,
    polygon: Polygon,
) -> Optional[float]:
    """Check if the segment (p1 -> p2) passes through `polygon`.

    Returns the normalized fraction in [0.0, 1.0] of the intersection midpoint
    along (p1 -> p2), or None if the segment does not intersect the polygon.
    """
    if p1_lon == p2_lon and p1_lat == p2_lat:
        return None
    segment = LineString([(p1_lon, p1_lat), (p2_lon, p2_lat)])
    return _intersection_fraction(segment, polygon)

