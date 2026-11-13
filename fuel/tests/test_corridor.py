from django.test import TestCase

from fuel.models import FuelStation
from fuel.services.corridor import (
    Candidate,
    RouteGrid,
    Station,
    cheapest_per_mile,
    load_stations,
    stations_along_route,
)
from fuel.services.geometry import cumulative_miles


def straight_route(lat=35.0, lon_from=-95.0, lon_to=-90.0, step=0.01):
    """A due-east line, which makes route miles easy to reason about."""
    n = int(round((lon_to - lon_from) / step)) + 1
    points = [(lat, lon_from + i * step) for i in range(n)]
    return points, cumulative_miles(points)


def make_station(**kwargs):
    defaults = {
        "opis_id": 1,
        "name": "Test stop",
        "address": "I-40",
        "city": "Somewhere",
        "state": "OK",
        "price": "3.500",
        "latitude": 35.0,
        "longitude": -93.0,
    }
    return FuelStation.objects.create(**{**defaults, **kwargs})


class CorridorTests(TestCase):
    def test_finds_a_stop_beside_the_route_and_places_it(self):
        points, cumulative = straight_route()
        on_route = Station(
            opis_id=1, name="On route", address="", city="A", state="OK",
            price=3.0, lat=35.02, lon=-93.0,
        )
        found = stations_along_route(points, cumulative, 15.0, [on_route])
        self.assertEqual(len(found), 1)
        self.assertLess(found[0].detour_mi, 2.0)
        # Two degrees of longitude east of the start, at 35 N.
        self.assertAlmostEqual(found[0].route_mile, 113.0, delta=2.0)

    def test_ignores_a_stop_outside_the_corridor(self):
        points, cumulative = straight_route()
        far = Station(
            opis_id=2, name="Far away", address="", city="B", state="OK",
            price=3.0, lat=36.0, lon=-93.0,   # about 69 miles north
        )
        self.assertEqual(stations_along_route(points, cumulative, 15.0, [far]), [])
        self.assertEqual(len(stations_along_route(points, cumulative, 80.0, [far])), 1)

    def test_grid_cells_grow_with_the_corridor(self):
        points, cumulative = straight_route()
        self.assertGreater(
            RouteGrid(points, cumulative, 60.0).cell_deg,
            RouteGrid(points, cumulative, 15.0).cell_deg,
        )

    def test_results_come_back_in_drive_order(self):
        points, cumulative = straight_route()
        stops = [
            Station(opis_id=i, name=str(i), address="", city="C", state="OK",
                    price=3.0, lat=35.0, lon=lon)
            for i, lon in enumerate([-91.0, -94.0, -92.5])
        ]
        found = stations_along_route(points, cumulative, 15.0, stops)
        self.assertEqual([c.route_mile for c in found], sorted(c.route_mile for c in found))

    def test_reads_located_stations_from_the_database(self):
        make_station(opis_id=10)
        make_station(opis_id=11, latitude=None, longitude=None)
        loaded = load_stations()
        self.assertEqual([s.opis_id for s in loaded], [10])

    def test_cache_notices_new_rows(self):
        make_station(opis_id=20)
        self.assertEqual(len(load_stations()), 1)
        make_station(opis_id=21, city="Elsewhere")
        self.assertEqual(len(load_stations()), 2)


class ThinningTests(TestCase):
    def candidate(self, mile, price, detour=1.0):
        station = Station(
            opis_id=int(mile * 10 + price), name="s", address="", city="C",
            state="OK", price=price, lat=0.0, lon=0.0,
        )
        return Candidate(station=station, route_mile=mile, detour_mi=detour)

    def test_keeps_only_the_cheapest_stop_in_a_town(self):
        # Every stop in a town shares the town's coordinates, so they all land
        # on the same point of the route.
        thinned = cheapest_per_mile(
            [self.candidate(120.0, 3.9), self.candidate(120.0, 3.1), self.candidate(120.0, 3.5)]
        )
        self.assertEqual([c.price for c in thinned], [3.1])

    def test_breaks_a_price_tie_on_the_shorter_detour(self):
        thinned = cheapest_per_mile(
            [self.candidate(50.0, 3.0, detour=9.0), self.candidate(50.0, 3.0, detour=2.0)]
        )
        self.assertEqual(thinned[0].detour_mi, 2.0)

    def test_leaves_separate_places_alone(self):
        thinned = cheapest_per_mile([self.candidate(10.0, 3.0), self.candidate(60.0, 4.0)])
        self.assertEqual(len(thinned), 2)
