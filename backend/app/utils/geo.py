"""Geographic utility functions."""

import math
from typing import Optional

from shapely.geometry import LineString, MultiPoint, Point, Polygon

EARTH_RADIUS_KM = 6371.0


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

