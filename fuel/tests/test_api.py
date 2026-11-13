import uuid
from unittest import mock

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from fuel.models import FuelStation, RoutePlan
from fuel.services.geo import GeoError, Place, Route
from fuel.services.geometry import cumulative_miles

# A due-east line at 35 N, roughly 285 miles long.
POINTS = [(35.0, -95.0 + i * 0.01) for i in range(501)]
CUMULATIVE = cumulative_miles(POINTS)

DALLAS = Place(query="Dallas, TX", name="Dallas, TX", lat=35.0, lon=-95.0)
MEMPHIS = Place(query="Memphis, TN", name="Memphis, TN", lat=35.0, lon=-90.0)


def fake_route(*args, **kwargs):
    return Route(
        distance_mi=CUMULATIVE[-1],
        duration_h=5.0,
        points=POINTS,
        cumulative_mi=CUMULATIVE,
    )


def fake_geocode(query, **kwargs):
    return (DALLAS if "Dallas" in query else MEMPHIS), 1


class ApiTestCase(TestCase):
    def setUp(self):
        FuelStation.objects.create(
            opis_id=1, name="Cheap Stop", address="I-40, EXIT 1", city="Early",
            state="OK", price="3.000", latitude=35.0, longitude=-94.5,
        )
        FuelStation.objects.create(
            opis_id=2, name="Dear Stop", address="I-40, EXIT 2", city="Late",
            state="AR", price="4.500", latitude=35.0, longitude=-91.0,
        )
        self.patches = [
            mock.patch("fuel.services.trip.route", side_effect=fake_route),
            mock.patch("fuel.services.trip.geocode", side_effect=fake_geocode),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def plan(self, **overrides):
        body = {"start": "Dallas, TX", "finish": "Memphis, TN"}
        body.update(overrides)
        return self.client.post(reverse("plan-route"), body, content_type="application/json")


class HealthTests(TestCase):
    def test_reports_how_much_data_is_loaded(self):
        FuelStation.objects.create(
            opis_id=1, name="A", city="X", state="OK", price="3.0",
            latitude=1.0, longitude=2.0,
        )
        body = self.client.get(reverse("health")).json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["truck_stops_located"], 1)

    def test_says_so_when_nothing_is_loaded(self):
        self.assertEqual(self.client.get(reverse("health")).json()["status"], "no fuel data loaded")


class PlanRouteTests(ApiTestCase):
    def test_plans_a_trip(self):
        resp = self.plan()
        self.assertEqual(resp.status_code, 201)
        body = resp.json()

        self.assertAlmostEqual(body["route"]["distance_mi"], 285, delta=3)
        self.assertGreater(body["fuel"]["total_cost_usd"], 0)
        self.assertEqual(body["fuel"]["stops"][0]["name"], "Cheap Stop")
        self.assertIn("geometry", body["route"])

    def test_buys_at_the_cheaper_stop(self):
        stops = {s["name"]: s for s in self.plan().json()["fuel"]["stops"]}
        # Everything is within one tank, so the dear stop should never be used.
        self.assertIn("Cheap Stop", stops)
        self.assertNotIn("Dear Stop", stops)

    def test_gallons_match_the_distance_driven(self):
        body = self.plan().json()
        self.assertAlmostEqual(
            body["fuel"]["gallons_burned"], body["route"]["distance_mi"] / 10, places=1
        )

    def test_reports_one_routing_call(self):
        meta = self.plan().json()["meta"]
        self.assertEqual(meta["routing_api_calls"], 1)
        self.assertEqual(meta["geocoding_api_calls"], 2)

    def test_stores_the_plan_and_links_to_its_map(self):
        body = self.plan().json()
        saved = RoutePlan.objects.get(pk=body["id"])
        self.assertEqual(saved.stop_count, body["fuel"]["stop_count"])
        self.assertEqual(self.client.get(body["map_url"]).status_code, 200)

    def test_geometry_can_be_left_out(self):
        self.assertNotIn("geometry", self.plan(geometry="none").json()["route"])

    def test_full_geometry_is_longer_than_the_simplified_one(self):
        full = self.plan(geometry="full").json()["route"]["geometry"]["coordinates"]
        simple = self.plan().json()["route"]["geometry"]["coordinates"]
        self.assertGreater(len(full), len(simple))

    def test_stop_penalty_defaults_to_the_setting(self):
        fuel = self.plan().json()["fuel"]
        self.assertEqual(fuel["stop_penalty_usd"], settings.STOP_PENALTY_USD)

    def test_stop_penalty_can_be_turned_off(self):
        resp = self.plan(stop_penalty_usd=0)
        self.assertEqual(resp.status_code, 201)
        self.assertEqual(resp.json()["fuel"]["stop_penalty_usd"], 0.0)

    def test_rejects_a_negative_stop_penalty(self):
        resp = self.plan(stop_penalty_usd=-5)
        self.assertEqual(resp.status_code, 400)
        self.assertIn("stop_penalty_usd", resp.json())

    def test_rejects_a_request_with_no_finish(self):
        resp = self.client.post(
            reverse("plan-route"), {"start": "Dallas, TX"}, content_type="application/json"
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("finish", resp.json())

    def test_passes_on_a_geocoding_failure(self):
        with mock.patch("fuel.services.trip.geocode", side_effect=GeoError("nope")):
            resp = self.plan()
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["detail"], "nope")

    def test_says_when_the_route_cannot_be_fuelled(self):
        FuelStation.objects.all().delete()
        resp = self.plan()
        self.assertEqual(resp.status_code, 422)
        self.assertIn("No truck stops", resp.json()["detail"])


class StoredPlanTests(ApiTestCase):
    def test_serves_a_stored_plan_again(self):
        created = self.plan().json()
        again = self.client.get(reverse("route-detail", args=[created["id"]])).json()
        self.assertEqual(again["fuel"]["total_cost_usd"], created["fuel"]["total_cost_usd"])

    def test_unknown_plan_is_a_404(self):
        resp = self.client.get(reverse("route-detail", args=[uuid.uuid4()]))
        self.assertEqual(resp.status_code, 404)

    def test_lists_recent_plans(self):
        self.plan()
        body = self.client.get(reverse("route-list")).json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["start_query"], "Dallas, TX")


class PageTests(ApiTestCase):
    def test_home_page_renders(self):
        self.assertEqual(self.client.get(reverse("index")).status_code, 200)

    def test_map_page_shows_the_stops(self):
        created = self.plan().json()
        page = self.client.get(reverse("route-map", args=[created["id"]]))
        self.assertContains(page, "Cheap Stop")
        self.assertContains(page, "plan-data")


class AssumptionsTests(TestCase):
    def test_states_the_vehicle_and_the_rules(self):
        body = self.client.get(reverse("assumptions")).json()
        self.assertEqual(body["vehicle"]["mpg"], 10)
        self.assertEqual(body["vehicle"]["range_mi"], 500)
        self.assertTrue(any("miles per gallon" in line for line in body["assumptions"]))
