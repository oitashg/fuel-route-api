from unittest import mock

import requests
from django.core.cache import cache
from django.test import SimpleTestCase

from fuel.services.geo import GeoError, Place, geocode, route, short_name

DALLAS = Place(query="a", name="Dallas, TX", lat=32.7767, lon=-96.797)
HOUSTON = Place(query="b", name="Houston, TX", lat=29.7604, lon=-95.3698)


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


OK_ROUTE = {
    "code": "Ok",
    "routes": [
        {
            # Three points along a line, encoded as polyline6.
            "geometry": "_grl_AnwxzfE_pR_pR_pR_pR",
            "distance": 160934.4,  # 100 miles
            "duration": 7200.0,
        }
    ],
}


class GeocodeTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_a_coordinate_pair_skips_the_geocoder(self):
        with mock.patch("fuel.services.geo.requests.get") as get:
            place, calls = geocode("32.7767, -96.7970")
        get.assert_not_called()
        self.assertEqual(calls, 0)
        self.assertEqual((place.lat, place.lon), (32.7767, -96.797))

    def test_rejects_an_impossible_coordinate(self):
        with self.assertRaises(GeoError):
            geocode("95.0,-200.0")

    def test_rejects_an_empty_query(self):
        with self.assertRaises(GeoError):
            geocode("   ")

    def test_second_lookup_of_a_name_is_free(self):
        payload = [{"lat": "32.7767", "lon": "-96.797", "display_name": "Dallas, Texas",
                    "address": {"city": "Dallas", "state": "Texas"}}]
        with mock.patch("fuel.services.geo.requests.get",
                        return_value=FakeResponse(payload)) as get:
            first, calls_a = geocode("Dallas TX")
            second, calls_b = geocode("dallas tx")   # same place, different case
        self.assertEqual(get.call_count, 1)
        self.assertEqual((calls_a, calls_b), (1, 0))
        self.assertEqual(first.name, second.name)

    def test_says_so_when_nothing_matches(self):
        with mock.patch("fuel.services.geo.requests.get", return_value=FakeResponse([])):
            with self.assertRaisesMessage(GeoError, "No US location found"):
                geocode("Nowhere At All ZZ")

    def test_shortens_a_long_display_name(self):
        hit = {
            "display_name": "Dallas, Dallas County, Texas, 75201, United States",
            "address": {"city": "Dallas", "state": "Texas", "country_code": "us"},
        }
        self.assertEqual(short_name(hit), "Dallas, TX")


class RouteTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def test_decodes_the_geometry_and_converts_to_miles(self):
        with mock.patch("fuel.services.geo.requests.get",
                        return_value=FakeResponse(OK_ROUTE)):
            drive = route(DALLAS, HOUSTON)
        self.assertAlmostEqual(drive.distance_mi, 100.0, places=3)
        self.assertEqual(drive.duration_h, 2.0)
        self.assertEqual(len(drive.points), 3)
        self.assertEqual(len(drive.cumulative_mi), 3)
        self.assertFalse(drive.cached)

    def test_the_same_trip_again_costs_nothing(self):
        with mock.patch("fuel.services.geo.requests.get",
                        return_value=FakeResponse(OK_ROUTE)) as get:
            route(DALLAS, HOUSTON)
            again = route(DALLAS, HOUSTON)
        self.assertEqual(get.call_count, 1)
        self.assertTrue(again.cached)

    def test_explains_an_unroutable_pair(self):
        # OSRM reports this with a 400 and the reason in the body.
        body = FakeResponse({"code": "NoRoute", "message": "Impossible route"}, 400)
        with mock.patch("fuel.services.geo.requests.get", return_value=body):
            with self.assertRaisesMessage(GeoError, "No drivable route"):
                route(DALLAS, HOUSTON)

    def test_reports_an_outage_without_leaking_the_url(self):
        with mock.patch("fuel.services.geo.requests.get",
                        side_effect=requests.ConnectionError("boom")):
            with self.assertRaisesMessage(GeoError, "Could not reach the routing service"):
                route(DALLAS, HOUSTON)

    def test_handles_a_server_error(self):
        with mock.patch("fuel.services.geo.requests.get",
                        return_value=FakeResponse({}, 503)):
            with self.assertRaisesMessage(GeoError, "refused that request"):
                route(DALLAS, HOUSTON)
