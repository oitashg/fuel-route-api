"""Attach coordinates to the imported truck stops.

The price file has no latitude or longitude, so this fills them in once and
stores them. Planning a route then costs no geocoding at all.
"""

from __future__ import annotations

import requests
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from fuel.models import FuelStation
from fuel.services import gazetteer
from fuel.services.geo import _throttle


class Command(BaseCommand):
    help = "Geocode truck stops, using the bundled city table first."

    def add_arguments(self, parser):
        parser.add_argument(
            "--online",
            action="store_true",
            help="Fall back to Nominatim for cities the bundled table misses.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-geocode stations that already have coordinates.",
        )
        parser.add_argument("--limit", type=int, default=0)

    def handle(self, *args, **options):
        qs = FuelStation.objects.all()
        if not options["force"]:
            qs = qs.filter(latitude__isnull=True)
        qs = qs.order_by("state", "city")
        if options["limit"]:
            qs = qs[: options["limit"]]

        stations = list(qs)
        if not stations:
            self.stdout.write("Every truck stop already has coordinates.")
            return

        self.stdout.write(f"Geocoding {len(stations)} truck stops...")

        done: list[FuelStation] = []
        from_table = from_nominatim = 0
        unresolved: set[tuple[str, str]] = set()
        online_cache: dict[tuple[str, str], tuple[float, float] | None] = {}

        for station in stations:
            coord = gazetteer.lookup(station.city, station.state)
            source = FuelStation.GAZETTEER

            if coord is None and options["online"]:
                key = (station.city.strip().upper(), station.state)
                if key not in online_cache:
                    online_cache[key] = self._nominatim(station.city, station.state)
                coord = online_cache[key]
                source = FuelStation.NOMINATIM

            if coord is None:
                unresolved.add((station.city.strip(), station.state))
                continue

            station.latitude, station.longitude = coord
            station.geocode_source = source
            done.append(station)
            if source == FuelStation.GAZETTEER:
                from_table += 1
            else:
                from_nominatim += 1

        with transaction.atomic():
            FuelStation.objects.bulk_update(
                done, ["latitude", "longitude", "geocode_source"], batch_size=500
            )

        self.stdout.write(
            f"Located {len(done)}: {from_table} from the bundled table, "
            f"{from_nominatim} from Nominatim."
        )
        if unresolved:
            listing = ", ".join(f"{c}, {s}" for c, s in sorted(unresolved)[:10])
            self.stdout.write(
                self.style.WARNING(
                    f"{len(unresolved)} places could not be resolved: {listing}"
                    + ("" if options["online"] else " (try --online)")
                )
            )

    def _nominatim(self, city: str, state: str) -> tuple[float, float] | None:
        """One polite, rate-limited lookup. Only a few of these ever run."""
        _throttle()
        try:
            resp = requests.get(
                settings.NOMINATIM_URL,
                params={
                    "q": f"{city}, {state}",
                    "format": "jsonv2",
                    "limit": 1,
                    "countrycodes": "us",
                },
                headers={"User-Agent": settings.GEO_USER_AGENT},
                timeout=settings.GEO_TIMEOUT,
            )
            resp.raise_for_status()
            results = resp.json()
        except (requests.RequestException, ValueError) as exc:
            self.stderr.write(f"  {city}, {state}: lookup failed ({exc})")
            return None
        if not results:
            return None
        return float(results[0]["lat"]), float(results[0]["lon"])
