"""Clients for the two free OpenStreetMap services this project depends on.

Geocoding -> Nominatim (https://nominatim.openstreetmap.org)
Routing   -> OSRM      (https://router.project-osrm.org)

Neither needs an API key. Planning a trip costs exactly one OSRM call, and at
most two Nominatim calls -- only when the caller passes place names rather than
coordinates, and only when those names are not already cached. Repeating a
request costs nothing at all: the route is cached on the rounded endpoints.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from dataclasses import dataclass, field

import requests
from django.conf import settings
from django.core.cache import cache

from .geometry import METERS_PER_MILE, Point, cumulative_miles, decode_polyline

log = logging.getLogger(__name__)

# "35.2271,-80.8431" or "35.2271, -80.8431" -- skip the geocoder entirely.
COORD_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")

STATE_ABBR = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "district of columbia": "DC", "florida": "FL", "georgia": "GA", "hawaii": "HI",
    "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA",
    "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME",
    "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE",
    "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH",
    "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}


class GeoError(Exception):
    """An upstream map service failed, or found nothing usable."""


@dataclass
class Place:
    query: str
    name: str
    lat: float
    lon: float

    def as_dict(self) -> dict:
        return {"query": self.query, "name": self.name, "lat": self.lat, "lon": self.lon}


@dataclass
class Route:
    distance_mi: float
    duration_h: float
    points: list[Point] = field(repr=False)          # (lat, lon), drive order
    cumulative_mi: list[float] = field(repr=False)   # miles travelled at each point
    cached: bool = False


# Nominatim's usage policy asks for no more than one request a second.
_nominatim_lock = threading.Lock()
_last_nominatim_call = 0.0


def _throttle(min_interval: float = 1.05) -> None:
    global _last_nominatim_call
    wait = min_interval - (time.monotonic() - _last_nominatim_call)
    if wait > 0:
        time.sleep(wait)
    _last_nominatim_call = time.monotonic()


def geocode(query: str) -> tuple[Place, int]:
    """Resolve free text to a coordinate.

    Returns the place and the number of upstream calls it cost, so the view can
    report honestly how much work a request did.
    """
    query = (query or "").strip()
    if not query:
        raise GeoError("Location must not be empty.")

    literal = COORD_RE.match(query)
    if literal:
        lat, lon = float(literal.group(1)), float(literal.group(2))
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise GeoError(f"{query!r} is not a valid coordinate.")
        return Place(query=query, name=f"{lat:.4f}, {lon:.4f}", lat=lat, lon=lon), 0

    key = "geocode:" + hashlib.sha1(query.lower().encode()).hexdigest()
    hit = cache.get(key)
    if hit:
        return Place(**hit), 0

    with _nominatim_lock:
        hit = cache.get(key)
        if hit:
            return Place(**hit), 0
        _throttle()
        try:
            resp = requests.get(
                settings.NOMINATIM_URL,
                params={
                    "q": query,
                    "format": "jsonv2",
                    "limit": 1,
                    "addressdetails": 1,
                    "countrycodes": "us",  # the brief fixes both ends to the USA
                },
                headers={"User-Agent": settings.GEO_USER_AGENT, "Accept-Language": "en"},
                timeout=settings.GEO_TIMEOUT,
            )
            resp.raise_for_status()
            results = resp.json()
        except requests.RequestException as exc:
            raise GeoError(f"Could not reach the geocoding service: {exc}") from exc
        except ValueError as exc:
            raise GeoError("The geocoding service returned an invalid response.") from exc

    if not results:
        raise GeoError(
            f"No US location found matching {query!r}. Adding a state usually fixes it."
        )

    top = results[0]
    place = Place(
        query=query,
        name=short_name(top),
        lat=float(top["lat"]),
        lon=float(top["lon"]),
    )
    cache.set(key, place.__dict__, timeout=None)
    return place, 1


def short_name(hit: dict) -> str:
    """Trim Nominatim's very long display_name down to 'City, ST'."""
    display = hit.get("display_name", "")
    address = hit.get("address") or {}
    city = (
        address.get("city")
        or address.get("town")
        or address.get("village")
        or address.get("hamlet")
        or address.get("county")
        or display.split(",")[0].strip()
    )
    state = STATE_ABBR.get((address.get("state") or "").strip().lower(), "")
    return ", ".join(p for p in (city, state) if p) or display


def route(start: Place, finish: Place) -> Route:
    """Driving route between two places -- one OSRM call, then cached.

    The geometry comes back as polyline6 and is decoded locally, which keeps
    the transfer small on long routes without losing any resolution.
    """
    key = "route:{:.4f},{:.4f}:{:.4f},{:.4f}".format(
        start.lat, start.lon, finish.lat, finish.lon
    )
    cached = cache.get(key)
    if cached:
        points = decode_polyline(cached["geometry"])
        return Route(
            distance_mi=cached["distance_mi"],
            duration_h=cached["duration_h"],
            points=points,
            cumulative_mi=cumulative_miles(points),
            cached=True,
        )

    url = (
        f"{settings.OSRM_URL}/route/v1/driving/"
        f"{start.lon:.6f},{start.lat:.6f};{finish.lon:.6f},{finish.lat:.6f}"
    )
    # Logged so that "one call per trip" is something you can watch happen.
    log.info("OSRM call: %s -> %s", start.name, finish.name)
    try:
        resp = requests.get(
            url,
            params={"overview": "full", "geometries": "polyline6", "steps": "false"},
            headers={"User-Agent": settings.GEO_USER_AGENT},
            timeout=settings.GEO_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise GeoError(f"Could not reach the routing service: {exc}") from exc

    # OSRM says "there is no such route" with a 400 and an explanation in the
    # body, so the body is worth reading before deciding what went wrong.
    try:
        payload = resp.json()
    except ValueError:
        payload = {}

    code = payload.get("code")
    if code in {"NoRoute", "NoSegment"}:
        raise GeoError(
            "No drivable route connects those two places. Check that both ends "
            "are on the US road network -- Hawaii and Puerto Rico are not."
        )
    if resp.status_code >= 400:
        raise GeoError(
            f"The routing service refused that request ({code or resp.status_code})."
        )
    if code != "Ok" or not payload.get("routes"):
        raise GeoError("The routing service returned an invalid response.")

    best = payload["routes"][0]
    geometry = best["geometry"]
    points = decode_polyline(geometry)
    if len(points) < 2:
        raise GeoError("The routing service returned an empty route.")

    cache.set(
        key,
        {
            "geometry": geometry,
            "distance_mi": best["distance"] / METERS_PER_MILE,
            "duration_h": best["duration"] / 3600.0,
        },
        timeout=settings.CACHES["default"]["TIMEOUT"],
    )

    return Route(
        distance_mi=best["distance"] / METERS_PER_MILE,
        duration_h=best["duration"] / 3600.0,
        points=points,
        cumulative_mi=cumulative_miles(points),
    )
