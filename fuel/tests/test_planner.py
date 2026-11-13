import functools
import random

from django.test import SimpleTestCase

from fuel.services.corridor import Candidate, Station
from fuel.services.planner import PlanningError, plan_fuel_stops


def stop_at(mile, price):
    station = Station(
        opis_id=int(mile * 100),
        name=f"stop at {mile}",
        address="",
        city="Somewhere",
        state="TX",
        price=float(price),
        lat=0.0,
        lon=0.0,
    )
    return Candidate(station=station, route_mile=float(mile), detour_mi=0.0)


def brute_force(positions, prices, total, tank, penalty=0):
    """Exhaustive search, for checking the planner against.

    Tries every station as the first fill, then every whole-mile purchase at
    every station after it, charging ``penalty`` for each station that sells
    anything. Only usable on tiny instances, which is the point.
    """

    @functools.lru_cache(maxsize=None)
    def best_from(i, fuel):
        here = positions[i]
        best = float("inf")
        for buy in range(0, tank - fuel + 1):
            tank_now = fuel + buy
            cost = buy * prices[i] + (penalty if buy else 0)
            if here + tank_now >= total:
                best = min(best, cost)
            for j in range(i + 1, len(positions)):
                if positions[j] - here <= tank_now:
                    best = min(best, cost + best_from(j, tank_now - (positions[j] - here)))
        return best

    overall = float("inf")
    for i, start in enumerate(positions):
        if start <= tank:
            rest = best_from(i, 0)
            if rest < float("inf"):
                overall = min(overall, start * prices[i] + rest)
    return None if overall == float("inf") else float(overall)


class PlannerTests(SimpleTestCase):
    def plan(self, stops, total, tank=500.0, mpg=10.0):
        return plan_fuel_stops(stops, total, range_mi=tank, mpg=mpg)

    def test_buys_the_whole_trip_at_the_only_stop(self):
        plan = self.plan([stop_at(0, 4.0)], 200.0)
        self.assertEqual(len(plan.stops), 1)
        self.assertIsNone(plan.departure_fill)
        self.assertAlmostEqual(plan.gallons_purchased, 20.0)
        self.assertAlmostEqual(plan.total_cost_usd, 80.0)

    def test_prices_the_departure_tank_at_the_first_stop(self):
        plan = self.plan([stop_at(100, 3.0)], 300.0)
        # 100 miles were driven to reach the stop and 200 remain.
        self.assertAlmostEqual(plan.departure_fill.gallons, 10.0)
        self.assertAlmostEqual(plan.stops[0].gallons, 20.0)
        self.assertAlmostEqual(plan.total_cost_usd, 90.0)

    def test_fills_the_tank_when_nothing_cheaper_is_in_reach(self):
        # Mile 0 is the cheapest thing in the first tankful, and the bargain at
        # mile 900 is well out of reach, so it fills right up and runs for the
        # furthest stop it can get to.
        plan = self.plan([stop_at(0, 3.0), stop_at(450, 4.0), stop_at(900, 2.0)], 1300.0)
        self.assertAlmostEqual(plan.stops[0].gallons, 50.0)
        self.assertEqual(plan.stops[0].candidate.route_mile, 0.0)

    def test_buys_the_minimum_at_a_dear_stop(self):
        plan = self.plan([stop_at(0, 3.0), stop_at(450, 4.0), stop_at(900, 2.0)], 1300.0)
        by_mile = {s.candidate.route_mile: s for s in plan.stops}
        # Mile 450 is dear, so it buys only enough to reach the cheap stop...
        self.assertAlmostEqual(by_mile[450.0].gallons, 40.0)
        # ...and the cheap stop buys only enough to finish.
        self.assertAlmostEqual(by_mile[900.0].gallons, 40.0)
        self.assertAlmostEqual(plan.total_cost_usd, 50 * 3.0 + 40 * 4.0 + 40 * 2.0)

    def test_never_arrives_with_fuel_it_did_not_need(self):
        plan = self.plan([stop_at(5, 3.0), stop_at(300, 2.5)], 600.0)
        self.assertAlmostEqual(plan.gallons_remaining, 0.0, places=6)
        self.assertAlmostEqual(plan.gallons_burned, 60.0)

    def test_gallons_balance(self):
        plan = self.plan([stop_at(20, 3.0), stop_at(480, 2.0), stop_at(900, 4.0)], 1300.0)
        self.assertAlmostEqual(
            plan.gallons_purchased, plan.gallons_burned + plan.gallons_remaining, places=6
        )
        spent = sum(s.cost_usd for s in plan.stops)
        if plan.departure_fill:
            spent += plan.departure_fill.cost_usd
        self.assertAlmostEqual(plan.total_cost_usd, spent, places=6)

    def test_a_stop_penalty_drops_a_pointless_top_up(self):
        # Mile 0 fills the tank and only reaches mile 500. The stop five miles
        # further on is a few cents cheaper, so the pure price optimum buys half
        # a gallon at mile 500 just to get there.
        stops = [stop_at(0, 3.00), stop_at(500, 3.50), stop_at(505, 3.45)]

        cheapest = plan_fuel_stops(stops, 1000.0, range_mi=500.0, mpg=10.0)
        self.assertEqual(len(cheapest.stops), 3)
        self.assertAlmostEqual(cheapest.stops[1].gallons, 0.5)

        sensible = plan_fuel_stops(
            stops, 1000.0, range_mi=500.0, mpg=10.0, stop_penalty_usd=10.0
        )
        self.assertEqual(len(sensible.stops), 2)
        self.assertAlmostEqual(sensible.total_cost_usd, 50 * 3.00 + 50 * 3.50)
        # The penalty steers the plan but is not part of the fuel bill.
        self.assertGreater(sensible.total_cost_usd, cheapest.total_cost_usd)

    def test_a_stop_penalty_still_takes_a_real_saving(self):
        # Ten dollars a stop is nothing next to a dollar a gallon on 40 gallons.
        stops = [stop_at(0, 3.0), stop_at(450, 4.0), stop_at(900, 2.0)]
        plan = plan_fuel_stops(stops, 1300.0, range_mi=500.0, mpg=10.0, stop_penalty_usd=10.0)
        self.assertEqual([s.candidate.route_mile for s in plan.stops], [0.0, 450.0, 900.0])

    def test_rejects_a_route_with_no_stops(self):
        with self.assertRaisesMessage(PlanningError, "No truck stops"):
            self.plan([], 100.0)

    def test_rejects_a_first_stop_beyond_the_tank(self):
        with self.assertRaisesMessage(PlanningError, "further than the 500 mile range"):
            self.plan([stop_at(600, 3.0)], 900.0)

    def test_rejects_a_gap_wider_than_the_tank(self):
        with self.assertRaisesMessage(PlanningError, "stretch with no truck stop"):
            self.plan([stop_at(10, 3.0), stop_at(900, 3.0)], 1000.0)

    def test_rejects_a_destination_out_of_reach_of_the_last_stop(self):
        with self.assertRaisesMessage(PlanningError, "short of the destination"):
            self.plan([stop_at(10, 3.0)], 800.0)


class OptimalityTests(SimpleTestCase):
    """The planner has to match an exhaustive search, not merely look sensible."""

    def test_matches_brute_force_on_random_routes(self):
        self.check_random_routes(seed=1234, penalties=[0])

    def test_matches_brute_force_with_a_stop_penalty(self):
        self.check_random_routes(seed=99, penalties=[1, 3, 8, 25])

    def check_random_routes(self, seed, penalties):
        rng = random.Random(seed)
        for _ in range(250):
            penalty = rng.choice(penalties)
            total = rng.randint(8, 40)
            tank = rng.randint(5, 20)
            count = rng.randint(1, 7)
            positions = sorted(rng.sample(range(total), count))
            prices = [rng.randint(1, 9) for _ in range(count)]

            stops = [stop_at(p, c) for p, c in zip(positions, prices)]
            try:
                # mpg of 1 keeps gallons and miles interchangeable, so the
                # brute force can work in whole numbers.
                plan = plan_fuel_stops(
                    stops,
                    float(total),
                    range_mi=float(tank),
                    mpg=1.0,
                    stop_penalty_usd=float(penalty),
                )
                got = plan.total_cost_usd + penalty * len(plan.stops)
            except PlanningError:
                got = None

            expected = brute_force(tuple(positions), tuple(prices), total, tank, penalty)
            self.assertEqual(
                got is None,
                expected is None,
                msg=f"feasibility differs for {positions} {prices} total={total} tank={tank}",
            )
            if expected is not None:
                self.assertAlmostEqual(
                    got,
                    expected,
                    places=6,
                    msg=f"cost differs for {positions} {prices} total={total} "
                    f"tank={tank} penalty={penalty}",
                )
