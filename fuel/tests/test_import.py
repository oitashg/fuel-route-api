import tempfile
from decimal import Decimal
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from fuel.models import FuelStation

CSV = """OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price
7,WOODSHED,"I-44, EXIT 283",Big Cabin,OK,307,3.00
7,WOODSHED OF BIG CABIN,"I-44, EXIT 283",Big Cabin,OK,307,3.20
9,KWIK TRIP #796,"I-94, EXIT 143",Tomah,WI,420,3.30
629,FLYING J #850,TCH-16,Edmonton,AB,80,4.40
77,BROKEN ROW,US-13,Laurel,DE,243,not-a-price
88,ZERO PRICE,US-13,Laurel,DE,243,0
"""


class LoadFuelPricesTests(TestCase):
    def run_import(self, body=CSV):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "prices.csv"
            path.write_text(body, encoding="utf-8")
            out = StringIO()
            call_command("load_fuel_prices", str(path), stdout=out)
        return out.getvalue()

    def test_folds_repeated_samples_onto_one_truck_stop(self):
        self.run_import()
        stop = FuelStation.objects.get(opis_id=7)
        self.assertEqual(stop.price_samples, 2)
        self.assertEqual(stop.price, Decimal("3.1000"))

    def test_keeps_the_fullest_spelling_of_the_name(self):
        self.run_import()
        self.assertEqual(FuelStation.objects.get(opis_id=7).name, "WOODSHED OF BIG CABIN")

    def test_skips_truck_stops_outside_the_usa(self):
        output = self.run_import()
        self.assertFalse(FuelStation.objects.filter(opis_id=629).exists())
        self.assertIn("1 rows outside the USA", output)

    def test_skips_rows_it_cannot_parse(self):
        output = self.run_import()
        self.assertFalse(FuelStation.objects.filter(opis_id__in=[77, 88]).exists())
        self.assertIn("2 rows that could not be parsed", output)

    def test_reimport_updates_prices_without_losing_coordinates(self):
        self.run_import()
        FuelStation.objects.filter(opis_id=9).update(latitude=43.97, longitude=-90.5)

        self.run_import(CSV.replace("3.30", "3.90"))

        stop = FuelStation.objects.get(opis_id=9)
        self.assertEqual(stop.price, Decimal("3.9000"))
        self.assertEqual(stop.latitude, 43.97)

    def test_complains_about_a_missing_file(self):
        with self.assertRaises(CommandError):
            call_command("load_fuel_prices", "/no/such/file.csv", stdout=StringIO())


class GeocodeStationsTests(TestCase):
    def test_locates_stations_from_the_bundled_table(self):
        FuelStation.objects.create(
            opis_id=1, name="Woodshed", city="Big Cabin", state="OK", price="3.00"
        )
        out = StringIO()
        call_command("geocode_stations", stdout=out)

        stop = FuelStation.objects.get(opis_id=1)
        self.assertAlmostEqual(stop.latitude, 36.6, delta=0.2)
        self.assertAlmostEqual(stop.longitude, -95.3, delta=0.2)
        self.assertEqual(stop.geocode_source, FuelStation.GAZETTEER)

    def test_reports_places_it_cannot_find_and_leaves_them_alone(self):
        FuelStation.objects.create(
            opis_id=2, name="Nowhere", city="Nowhereville", state="OK", price="3.00"
        )
        out = StringIO()
        call_command("geocode_stations", stdout=out)

        self.assertIsNone(FuelStation.objects.get(opis_id=2).latitude)
        self.assertIn("could not be resolved", out.getvalue())

    def test_leaves_stations_that_already_have_coordinates(self):
        FuelStation.objects.create(
            opis_id=3, name="Set", city="Tomah", state="WI", price="3.00",
            latitude=1.0, longitude=2.0,
        )
        out = StringIO()
        call_command("geocode_stations", stdout=out)

        self.assertEqual(FuelStation.objects.get(opis_id=3).latitude, 1.0)
        self.assertIn("already has coordinates", out.getvalue())
