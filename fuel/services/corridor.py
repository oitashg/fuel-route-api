"""Finding the truck stops that sit on a given route.

The naive version of this -- measure every station against every point of the
line -- is 6,700 stations times ~9,000 route points, which is far too slow to
do inside a request. Instead the route points are dropped into a grid of
lat/lon cells and each station only looks at its own cell and the eight around
it, so the work is proportional to the number of stations.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from django.db.models import Count, Max

from ..models import FuelStation
from .geometry import Point, haversine_mi, resample

# One route point per mile is plenty: the error that introduces is at most half
# a mile, against a corridor measured in tens of miles.
SAMPLE_SPACING_MI = 1.0

# Degrees of latitude per mile, used to size the grid cells. Longitude degrees
# shrink towards the poles; 45 mi/degree is the worst case over the mainland
# USA (about 49 N), so sizing cells with it keeps the 3x3 search exhaustive.
MILES_PER_LON_DEGREE_MIN = 45.0


@dataclass(frozen=True)
class Station:
    """A located truck stop, flattened out of the database for fast scanning."""

    opis_id: int
    name: str
    address: str
    city: str
    state: str
    price: float
    lat: float
    lon: float


@dataclass(frozen=True)
class Candidate:
    """A truck stop that lies on the route, with where it lies."""

    station: Station
    route_mile: float   # distance from the start, along the route
    detour_mi: float    # straight-line distance from the route to the stop

    @property
    def price(self) -> float:
        return self.station.price


class StationCache:
    """Keeps the located stations in memory, reloading when the table changes."""

    def __init__(self) -> None:
        self._stamp: tuple | None = None
        self._stations: list[Station] = []

    def stations(self) -> list[Station]:
        agg = FuelStation.objects.filter(
            latitude__isnull=False, longitude__isnull=False
        ).aggregate(rows=Count("id"), touched=Max("updated_at"))
        stamp = (agg["rows"], agg["touched"])
        if stamp != self._stamp:
            self._stations = [
                Station(
                    opis_id=s.opis_id,
                    name=s.name,
                    address=s.address,
                    city=s.city,
                    state=s.state,
                    price=float(s.price),
                    lat=s.latitude,
                    lon=s.longitude,
                )
                for s in FuelStation.objects.filter(
                    latitude__isnull=False, longitude__isnull=False
                ).only(
                    "opis_id", "name", "address", "city", "state",
                    "price", "latitude", "longitude",
                )
            ]
            self._stamp = stamp
        return self._stations


_cache = StationCache()


def load_stations() -> list[Station]:
    return _cache.stations()


class RouteGrid:
    """Grid index over the points of one route."""

    def __init__(self, points: list[Point], cumulative: list[float], radius_mi: float):
        self.cell_deg = max(0.5, radius_mi / MILES_PER_LON_DEGREE_MIN)
        self.points = points
        self.cumulative = cumulative
        self.cells: dict[tuple[int, int], list[int]] = {}
        for i in resample(points, cumulative, SAMPLE_SPACING_MI):
            lat, lon = points[i]
            self.cells.setdefault(self._cell(lat, lon), []).append(i)

    def _cell(self, lat: float, lon: float) -> tuple[int, int]:
        return (
            int(math.floor(lat / self.cell_deg)),
            int(math.floor(lon / self.cell_deg)),
        )

    def nearest(self, lat: float, lon: float, limit_mi: float) -> tuple[float, int] | None:
        """Closest indexed route point to (lat, lon), if one is within limit."""
        cy, cx = self._cell(lat, lon)
        best: tuple[float, int] | None = None
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for i in self.cells.get((cy + dy, cx + dx), ()):
                    plat, plon = self.points[i]
                    d = haversine_mi(lat, lon, plat, plon)
                    if d <= limit_mi and (best is None or d < best[0]):
                        best = (d, i)
        return best


def stations_along_route(
    points: list[Point],
    cumulative: list[float],
    corridor_mi: float,
    stations: list[Station] | None = None,
) -> list[Candidate]:
    """Every truck stop within ``corridor_mi`` of the route, in drive order."""
    stations = load_stations() if stations is None else stations
    if len(points) < 2 or not stations:
        return []

    grid = RouteGrid(points, cumulative, corridor_mi)
    found: list[Candidate] = []
    for station in stations:
        hit = grid.nearest(station.lat, station.lon, corridor_mi)
        if hit is None:
            continue
        detour, idx = hit
        found.append(
            Candidate(station=station, route_mile=cumulative[idx], detour_mi=detour)
        )

    found.sort(key=lambda c: (c.route_mile, c.price))
    return found


def cheapest_per_mile(candidates: list[Candidate]) -> list[Candidate]:
    """Thin the candidates down to the cheapest stop at each point on the line.

    Stations are placed at the centre of their town, so a town with four truck
    stops yields four candidates at the same mile marker. Only the cheapest can
    ever be the right answer, and dropping the rest keeps the planner's search
    window small.
    """
    best: dict[int, Candidate] = {}
    for c in candidates:
        key = int(round(c.route_mile * 10))  # tenth-of-a-mile buckets
        current = best.get(key)
        if current is None or (c.price, c.detour_mi) < (current.price, current.detour_mi):
            best[key] = c
    return [best[k] for k in sorted(best)]
