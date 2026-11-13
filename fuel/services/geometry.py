"""Geometry helpers for working with a driven route.

Everything here is plain arithmetic on (lat, lon) pairs -- no GEOS, no PostGIS,
so the project runs on a stock SQLite install. Distances are statute miles.
"""

from __future__ import annotations

import math

EARTH_RADIUS_MI = 3958.7613
METERS_PER_MILE = 1609.344

Point = tuple[float, float]  # (lat, lon)


def haversine_mi(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two points, in miles."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_MI * math.asin(math.sqrt(a))


def decode_polyline(encoded: str, precision: int = 6) -> list[Point]:
    """Decode an encoded-polyline string into (lat, lon) pairs.

    OSRM can hand back its geometry as polyline6, which is roughly a quarter
    the bytes of the equivalent GeoJSON for a cross-country route. Decoding it
    here is cheaper than downloading the long form.
    """
    factor = float(10**precision)
    points: list[Point] = []
    index = lat = lon = 0
    length = len(encoded)

    while index < length:
        for axis in range(2):
            shift = result = 0
            while True:
                if index >= length:
                    raise ValueError("truncated polyline")
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else (result >> 1)
            if axis == 0:
                lat += delta
            else:
                lon += delta
        points.append((lat / factor, lon / factor))

    return points


def cumulative_miles(points: list[Point]) -> list[float]:
    """Running distance along ``points``, starting at 0."""
    out = [0.0]
    total = 0.0
    for (lat1, lon1), (lat2, lon2) in zip(points, points[1:]):
        total += haversine_mi(lat1, lon1, lat2, lon2)
        out.append(total)
    return out


def resample(points: list[Point], cumulative: list[float], spacing_mi: float) -> list[int]:
    """Indices of ``points`` kept when thinning the line to ~``spacing_mi``.

    The corridor search only needs a representative set of points: dropping a
    40,000-vertex interstate down to one vertex per mile keeps the search cheap
    and costs at most half a mile of accuracy, against a corridor measured in
    tens of miles.
    """
    if not points:
        return []
    keep = [0]
    last = 0.0
    for i in range(1, len(points)):
        if cumulative[i] - last >= spacing_mi:
            keep.append(i)
            last = cumulative[i]
    if keep[-1] != len(points) - 1:
        keep.append(len(points) - 1)
    return keep


def simplify(points: list[Point], tolerance_deg: float) -> list[Point]:
    """Ramer-Douglas-Peucker, iterative so long routes cannot blow the stack.

    Used only to trim the geometry we hand back to a client; the mileage maths
    always runs on the full-resolution line.
    """
    if len(points) < 3:
        return list(points)

    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]

    while stack:
        start, end = stack.pop()
        if end <= start + 1:
            continue
        worst, worst_i = -1.0, start
        ax, ay = points[start][1], points[start][0]
        bx, by = points[end][1], points[end][0]
        dx, dy = bx - ax, by - ay
        span = dx * dx + dy * dy
        for i in range(start + 1, end):
            px, py = points[i][1], points[i][0]
            if span == 0:
                d = math.hypot(px - ax, py - ay)
            else:
                # Perpendicular offset from the chord, in degrees. Fine for a
                # display tolerance; we are not measuring anything with it.
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / span))
                d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
            if d > worst:
                worst, worst_i = d, i
        if worst > tolerance_deg:
            keep[worst_i] = True
            stack.append((start, worst_i))
            stack.append((worst_i, end))

    return [p for p, k in zip(points, keep) if k]
