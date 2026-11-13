"""Load the OPIS price file into the fuel_station table."""

from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from fuel.models import FuelStation

DEFAULT_FILE = (
    Path(__file__).resolve().parents[2] / "data" / "fuel-prices-for-be-assessment.csv"
)

# The file carries a few Canadian truck stops (AB, ON, BC...). The brief fixes
# both ends of the trip to the USA, so they are dropped rather than planned
# around.
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "DC", "FL", "GA", "HI",
    "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN",
    "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH",
    "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA",
    "WV", "WI", "WY",
}


class Command(BaseCommand):
    help = "Import truck stop fuel prices from the supplied CSV."

    def add_arguments(self, parser):
        parser.add_argument("path", nargs="?", default=str(DEFAULT_FILE))
        parser.add_argument(
            "--keep-foreign",
            action="store_true",
            help="Keep truck stops outside the USA instead of skipping them.",
        )

    def handle(self, *args, **options):
        path = Path(options["path"])
        if not path.exists():
            raise CommandError(f"No such file: {path}")

        rows, skipped_foreign, skipped_bad = self._read(path, options["keep_foreign"])
        if not rows:
            raise CommandError("The file contained no usable rows.")

        created, updated = self._save(rows)

        self.stdout.write(
            f"{created} new, {updated} updated, {len(rows)} truck stops in total."
        )
        if skipped_foreign:
            self.stdout.write(f"Skipped {skipped_foreign} rows outside the USA.")
        if skipped_bad:
            self.stdout.write(f"Skipped {skipped_bad} rows that could not be parsed.")

        missing = FuelStation.objects.filter(latitude__isnull=True).count()
        if missing:
            self.stdout.write(
                self.style.WARNING(
                    f"{missing} truck stops still have no coordinates. "
                    f"Run: manage.py geocode_stations"
                )
            )

    def _read(self, path: Path, keep_foreign: bool):
        """Fold the per-sample rows onto one record per truck stop."""
        grouped: dict[int, dict] = {}
        skipped_foreign = skipped_bad = 0

        # utf-8-sig: the export carries a byte order mark.
        with path.open(newline="", encoding="utf-8-sig") as fh:
            for raw in csv.DictReader(fh):
                try:
                    opis_id = int(raw["OPIS Truckstop ID"])
                    price = Decimal(raw["Retail Price"].strip())
                except (KeyError, TypeError, ValueError, InvalidOperation, AttributeError):
                    skipped_bad += 1
                    continue

                state = (raw.get("State") or "").strip().upper()
                if not keep_foreign and state not in US_STATES:
                    skipped_foreign += 1
                    continue
                if price <= 0:
                    skipped_bad += 1
                    continue

                name = (raw.get("Truckstop Name") or "").strip()
                entry = grouped.get(opis_id)
                if entry is None:
                    try:
                        rack_id = int(raw["Rack ID"])
                    except (KeyError, TypeError, ValueError):
                        rack_id = None
                    grouped[opis_id] = {
                        "opis_id": opis_id,
                        "name": name,
                        "address": (raw.get("Address") or "").strip(),
                        "city": (raw.get("City") or "").strip(),
                        "state": state,
                        "rack_id": rack_id,
                        "prices": [price],
                    }
                else:
                    entry["prices"].append(price)
                    # The same stop is spelled several ways across samples
                    # ("PILOT #1243" / "PILOT TRAVEL CENTER #1243"); keep the
                    # fullest version.
                    if len(name) > len(entry["name"]):
                        entry["name"] = name

        rows = []
        for entry in grouped.values():
            prices = entry.pop("prices")
            entry["price"] = (sum(prices) / len(prices)).quantize(Decimal("0.0001"))
            entry["price_samples"] = len(prices)
            rows.append(entry)
        return rows, skipped_foreign, skipped_bad

    @transaction.atomic
    def _save(self, rows: list[dict]) -> tuple[int, int]:
        existing = set(FuelStation.objects.values_list("opis_id", flat=True))
        objects = [FuelStation(**row) for row in rows]
        # Coordinates are deliberately left out of update_fields: re-importing
        # prices must not throw away work the geocoder has already done.
        FuelStation.objects.bulk_create(
            objects,
            update_conflicts=True,
            unique_fields=["opis_id"],
            update_fields=[
                "name", "address", "city", "state", "rack_id",
                "price", "price_samples", "updated_at",
            ],
            batch_size=500,
        )
        created = sum(1 for row in rows if row["opis_id"] not in existing)
        return created, len(rows) - created
