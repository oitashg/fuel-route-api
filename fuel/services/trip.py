"""Putting a whole trip together.

Geocode both ends, ask OSRM for the drive, find the truck stops that sit on
it, work out where to buy fuel, and shape all of that into the JSON the API
hands back.

Upstream cost of one planned trip: one OSRM call, plus one Nominatim call per
place name that is not a literal coordinate and not already cached. Nothing in
the fuel side of the work touches the network -- the prices and the
coordinates are already in the database.
"""

from __future__ import annotations

import logging
import time

from django.conf import settings

from .corridor import Candidate, cheapest_per_mile, stations_along_route
from .geo import GeoError, geocode, route
from .geometry import simplify
from .planner import FuelPlan, PlanningError, plan_fuel_stops

log = logging.getLogger(__name__)

# How hard to thin the line before sending it. 0.0025 degrees is about 150
# metres, which is invisible on a map of a cross-country drive but takes a
# megabyte off the response.
GEOMETRY_TOLERANCE_DEG = 0.0025


def assumptions() -> list[str]:
    mpg = settings.VEHICLE_MPG
    rng = settings.VEHICLE_RANGE_MI
    return [
        f"The vehicle does {mpg:g} miles per gallon and has a range of {rng:g} "
        f"miles, so a full tank is {rng / mpg:g} gallons.",
        "The fuel already in the tank at the origin is paid for too, at the price "
        "of the first stop, since the price file says nothing about where the "
        "vehicle last filled up. It is reported separately as the departure tank.",
        "It never buys more fuel than it needs, so the figure quoted is what the "
        "drive actually costs in fuel.",
        f"Each stop is treated as costing ${settings.STOP_PENALTY_USD:g} in "
        "driver time, so it will not pull off the highway to save a few cents. "
        "That only affects which stops are chosen; it is not in the fuel total. "
        "Send stop_penalty_usd: 0 for the cheapest fuel regardless of stops.",
        f"A truck stop counts as being on the route if it is within "
        f"{settings.CORRIDOR_MI:g} miles of it. Pulling off the highway and back "
        f"on is not added to the distance driven.",
        "Prices come from the supplied OPIS file. Where a truck stop appears more "
        "than once, the mean of its prices is used.",
        "Truck stops are placed at the centre of their town, which is as precise "
        "as the supplied file allows.",
    ]


def plan_trip(
    start_query: str,
    finish_query: str,
    geometry: str = "simplified",
    stop_penalty_usd: float | None = None,
) -> dict:
    """Plan a trip and return the API payload for it."""
    started = time.monotonic()
    if stop_penalty_usd is None:
        stop_penalty_usd = settings.STOP_PENALTY_USD

    start, calls_a = geocode(start_query)
    finish, calls_b = geocode(finish_query)
    drive = route(start, finish)
    if drive.distance_mi < 0.1:
        raise GeoError("The start and the finish are the same place.")

    candidates, corridor_mi, plan = _solve(
        drive.points, drive.cumulative_mi, drive.distance_mi, stop_penalty_usd
    )

    payload = {
        "start": start.as_dict(),
        "finish": finish.as_dict(),
        "route": _route_payload(drive, geometry),
        "fuel": _fuel_payload(plan, drive.distance_mi),
        "vehicle": {
            "mpg": settings.VEHICLE_MPG,
            "range_mi": settings.VEHICLE_RANGE_MI,
            "tank_gallons": round(settings.VEHICLE_RANGE_MI / settings.VEHICLE_MPG, 2),
        },
        "assumptions": assumptions(),
        "meta": {
            "routing_api_calls": 0 if drive.cached else 1,
            "geocoding_api_calls": calls_a + calls_b,
            "route_from_cache": drive.cached,
            "corridor_mi": corridor_mi,
            "stations_on_route": len(candidates),
            "route_points": len(drive.points),
            "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
        },
    }
    return payload


def _solve(
    points, cumulative, distance_mi, stop_penalty_usd
) -> tuple[list[Candidate], float, FuelPlan]:
    """Find stops, widening the corridor once if the first pass runs dry.

    Rural stretches of interstate can have nothing within the usual corridor,
    and a town centre can sit a fair way off the highway it serves. Rather than
    fail outright, look further out before giving up.
    """
    attempts = [settings.CORRIDOR_MI]
    if settings.CORRIDOR_FALLBACK_MI > settings.CORRIDOR_MI:
        attempts.append(settings.CORRIDOR_FALLBACK_MI)

    last_error: PlanningError | None = None
    for corridor_mi in attempts:
        candidates = stations_along_route(points, cumulative, corridor_mi)
        try:
            plan = plan_fuel_stops(
                cheapest_per_mile(candidates),
                distance_mi,
                range_mi=settings.VEHICLE_RANGE_MI,
                mpg=settings.VEHICLE_MPG,
                stop_penalty_usd=stop_penalty_usd,
            )
        except PlanningError as exc:
            last_error = exc
            log.info("no plan within %.0f mi of the route: %s", corridor_mi, exc)
            continue
        return candidates, corridor_mi, plan

    raise last_error or PlanningError("No fuel plan could be built for this route.")


def _route_payload(drive, geometry: str) -> dict:
    out = {
        "distance_mi": round(drive.distance_mi, 1),
        "duration_h": round(drive.duration_h, 2),
    }
    if geometry == "none":
        return out

    points = drive.points
    if geometry != "full":
        points = simplify(points, GEOMETRY_TOLERANCE_DEG)
    out["geometry"] = {
        # GeoJSON order is (lon, lat), the reverse of everything else here.
        "type": "LineString",
        "coordinates": [[round(lon, 5), round(lat, 5)] for lat, lon in points],
    }
    return out


def _fuel_payload(plan: FuelPlan, distance_mi: float) -> dict:
    stops = []
    for i, stop in enumerate(plan.stops, start=1):
        station = stop.candidate.station
        next_mile = (
            plan.stops[i].candidate.route_mile if i < len(plan.stops) else distance_mi
        )
        stops.append(
            {
                "sequence": i,
                "opis_id": station.opis_id,
                "name": station.name,
                "address": station.address,
                "city": station.city,
                "state": station.state,
                "lat": station.lat,
                "lon": station.lon,
                "price_per_gal": round(station.price, 3),
                "gallons": round(stop.gallons, 2),
                "cost_usd": round(stop.cost_usd, 2),
                "route_mile": round(stop.candidate.route_mile, 1),
                "miles_to_next_stop": round(next_mile - stop.candidate.route_mile, 1),
                "detour_mi": round(stop.candidate.detour_mi, 1),
            }
        )

    departure = None
    if plan.departure_fill is not None:
        fill = plan.departure_fill
        departure = {
            "gallons": round(fill.gallons, 2),
            "cost_usd": round(fill.cost_usd, 2),
            "price_per_gal": round(fill.candidate.price, 3),
            "miles_to_first_stop": round(fill.candidate.route_mile, 1),
            "priced_at": fill.candidate.station.name,
        }

    return {
        "total_cost_usd": round(plan.total_cost_usd, 2),
        "departure_tank": departure,
        "gallons_purchased": round(plan.gallons_purchased, 2),
        "gallons_burned": round(plan.gallons_burned, 2),
        "gallons_in_tank_on_arrival": round(plan.gallons_remaining, 2),
        "average_price_per_gal": round(plan.average_price, 3),
        "stop_count": len(stops),
        "stop_penalty_usd": plan.stop_penalty_usd,
        "stops": stops,
    }
