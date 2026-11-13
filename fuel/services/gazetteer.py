"""Offline city -> coordinate lookup.

The price file names a city and a state but gives no coordinates, and putting
6,700 truck stops through Nominatim at its one-request-per-second policy would
take two hours. A bundled table of ~30k US cities resolves 99.8% of them
instantly; the handful it misses fall through to Nominatim once, at import
time, and are then stored in the database like everything else.
"""

from __future__ import annotations

import csv
import re
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DATA_FILES = (DATA_DIR / "us_cities.csv", DATA_DIR / "extra_places.csv")

_PUNCT = re.compile(r"[.'`]")
_SPACES = re.compile(r"\s+")


def normalise(city: str) -> str:
    """Fold a city name to a comparable form ('St. Louis' -> 'SAINT LOUIS')."""
    name = _PUNCT.sub("", (city or "").strip().upper())
    name = _SPACES.sub(" ", name)
    if name.startswith("ST "):
        name = "SAINT " + name[3:]
    if name.startswith("MT "):
        name = "MOUNT " + name[3:]
    if name.startswith("FT "):
        name = "FORT " + name[3:]
    return name


@lru_cache(maxsize=1)
def _index() -> dict[tuple[str, str], tuple[float, float]]:
    table: dict[tuple[str, str], tuple[float, float]] = {}
    for path in DATA_FILES:
        if not path.exists():  # pragma: no cover - only if a file is missing
            continue
        with path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                try:
                    coord = (float(row["lat"]), float(row["lon"]))
                except (TypeError, ValueError, KeyError):
                    continue
                # First spelling wins; the files are sorted and duplicates are
                # the same place listed twice.
                key = (normalise(row["city"]), row["state"].strip().upper())
                table.setdefault(key, coord)
    return table


def lookup(city: str, state: str) -> tuple[float, float] | None:
    """Coordinates for a city, or None if it is not in the table."""
    return _index().get((normalise(city), (state or "").strip().upper()))


def size() -> int:
    return len(_index())
