"""Choosing where to buy fuel.

A vehicle with a fixed tank drives a line past truck stops with known prices
and has to reach the end. Without anything else to weigh, the cheapest plan is
the textbook greedy: buy just enough to reach the next cheaper stop, or fill
up if nothing in reach is cheaper. Run on real routes, that greedy is right
about the money and wrong about the driving. It happily pulls off the
interstate to buy one gallon because the stop ten miles on is four cents
dearer.

So every stop also carries a fixed penalty, ``stop_penalty_usd``, standing in
for the time it takes to pull off, fuel and get back on. The penalty decides
where to stop; it is never added to the fuel bill that gets reported. Set it
to zero and the plan is the pure price optimum again.

With a cost per stop the greedy is no longer optimal, so this is a dynamic
program instead. It rests on one observation about optimal plans (Khuller,
Malekian and Mestre, "To fill or not to fill", 2007): leaving a stop ``u`` for
the next stop ``v``, the vehicle either fills the tank at ``u`` or buys just
enough to arrive at ``v`` empty. Anything in between can be shifted one way or
the other without costing more. So the fuel on arrival at ``v`` is either zero
or "a full tank, less the drive from some earlier stop", which leaves a few
dozen states per stop rather than a continuum. Both kinds of move are cheap to
evaluate:

* arriving empty at ``v`` from ``u`` costs ``cost(state) + (d - fuel) * price``,
  so for each ``v`` it is a prefix minimum over the states at ``u`` sorted by
  fuel;
* filling up at ``u`` costs the same whatever ``v`` is, so it is worked out
  once per stop.

The destination is a node too, reachable only by arriving empty, which is what
keeps the plan from buying fuel it does not need.

The start. The price file does not cover every town -- out of Los Angeles the
first truck stop in it is over 200 miles away -- so the vehicle has to leave
with fuel already in the tank, and that fuel has to be paid for somewhere. It
is billed at the price of the first stop, the nearest thing to a market rate
that is actually known, and the vehicle reaches that stop empty. Any stop
within one tank of the origin can be the first; the program tries them all.
The departure tank is reported apart from what is bought at the stop itself,
so no single purchase is ever more than a tankful.

``fuel/tests/test_planner.py`` checks the result against an exhaustive search
on random instances, with and without a stop penalty.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass

from .corridor import Candidate

INF = float("inf")

# Breaks ties between equally priced plans in favour of fewer stops. Far too
# small to change which plan is cheapest.
TIE_BREAK_USD = 1e-7


class PlanningError(Exception):
    """No valid fuel plan exists for this route."""


@dataclass(frozen=True)
class Stop:
    candidate: Candidate
    gallons: float
    cost_usd: float


@dataclass
class FuelPlan:
    stops: list[Stop]
    departure_fill: Stop | None   # fuel in the tank at the origin, priced at stop 1
    total_cost_usd: float         # fuel only; the stop penalty is not money spent
    gallons_purchased: float
    gallons_burned: float
    gallons_remaining: float
    stop_penalty_usd: float = 0.0

    @property
    def average_price(self) -> float:
        if self.gallons_purchased <= 0:
            return 0.0
        return self.total_cost_usd / self.gallons_purchased


@dataclass
class _Node:
    """A place the vehicle can stop. The destination is one, priced at zero."""

    mile: float
    price: float
    candidate: Candidate | None


@dataclass
class _State:
    """Arriving at a node with ``fuel`` miles in the tank, for ``cost`` so far."""

    fuel: float
    cost: float
    came_from: tuple[int, int] | None   # (node, index of its state); None = origin
    bought_mi: float                    # bought at the previous node to get here


def plan_fuel_stops(
    candidates: list[Candidate],
    total_mi: float,
    *,
    range_mi: float,
    mpg: float,
    stop_penalty_usd: float = 0.0,
) -> FuelPlan:
    """Cheapest way to fuel a ``total_mi`` drive past ``candidates``."""
    if total_mi <= 0:
        raise PlanningError("The route has no length.")

    nodes = [
        _Node(c.route_mile, c.price, c) for c in candidates if 0 <= c.route_mile < total_mi
    ]
    nodes.sort(key=lambda n: n.mile)
    _check_reachable(nodes, total_mi, range_mi)

    nodes.append(_Node(total_mi, 0.0, None))
    end = len(nodes) - 1
    miles = [n.mile for n in nodes]
    penalty = stop_penalty_usd + TIE_BREAK_USD

    states: list[list[_State]] = [[] for _ in nodes]
    empty_at: list[int | None] = [None] * len(nodes)  # index of the fuel == 0 state

    def arrive(v: int, state: _State) -> None:
        if state.fuel <= 0:
            state.fuel = 0.0
            i = empty_at[v]
            if i is not None:
                if state.cost < states[v][i].cost:
                    states[v][i] = state
                return
            empty_at[v] = len(states[v])
        states[v].append(state)

    # Any stop within a tank of the origin can be the first, reached empty on
    # fuel bought at its own price.
    for f in range(end):
        if nodes[f].mile > range_mi:
            break
        cost = nodes[f].mile * nodes[f].price / mpg + penalty
        arrive(f, _State(0.0, cost, None, nodes[f].mile))

    for u in range(end):
        here = states[u]
        if not here:
            continue
        here.sort(key=lambda s: s.fuel)
        empty_at[u] = None  # positions have moved; nothing arrives at u any more
        rate = nodes[u].price / mpg
        fuels = [s.fuel for s in here]

        # Best state to leave from if the tank is to be topped up to d, for
        # every d: the prefix minimum of cost - fuel * rate.
        prefix: list[int] = []
        for i, s in enumerate(here):
            if not prefix or s.cost - s.fuel * rate < (
                here[prefix[-1]].cost - here[prefix[-1]].fuel * rate
            ):
                prefix.append(i)
            else:
                prefix.append(prefix[-1])
        full = prefix[-1]  # best state to fill the tank from

        last = bisect_right(miles, miles[u] + range_mi) - 1
        for v in range(u + 1, last + 1):
            d = miles[v] - miles[u]
            extra = 0.0 if v == end else penalty

            k = bisect_right(fuels, d + 1e-9) - 1
            if k >= 0:
                s = here[prefix[k]]
                buy = max(0.0, d - s.fuel)
                arrive(v, _State(0.0, s.cost + buy * rate + extra, (u, prefix[k]), buy))

            if v != end:
                s = here[full]
                buy = range_mi - s.fuel
                arrive(
                    v,
                    _State(range_mi - d, s.cost + buy * rate + extra, (u, full), buy),
                )

    if not states[end]:  # pragma: no cover - _check_reachable rules this out
        raise PlanningError("No fuel plan could be built for this route.")
    best = min(states[end], key=lambda s: s.cost)
    return _price_up(nodes, states, best, total_mi, mpg, stop_penalty_usd)


def _price_up(nodes, states, best, total_mi, mpg, stop_penalty_usd) -> FuelPlan:
    """Walk the chosen plan back from the destination and add up the fuel."""
    purchases: list[tuple[int, float]] = []
    state = best
    while state.came_from is not None:
        u, i = state.came_from
        purchases.append((u, state.bought_mi))
        state = states[u][i]
    first = state  # the state the origin produced
    purchases.reverse()
    first_node = purchases[0][0] if purchases else len(nodes) - 1

    total_cost = 0.0
    bought_mi = 0.0
    departure_fill = None
    if first.bought_mi > 0:
        candidate = nodes[first_node].candidate
        gallons = first.bought_mi / mpg
        cost = gallons * candidate.price
        departure_fill = Stop(candidate=candidate, gallons=gallons, cost_usd=cost)
        total_cost += cost
        bought_mi += first.bought_mi

    stops: list[Stop] = []
    for u, miles_bought in purchases:
        if miles_bought <= 1e-9:
            continue
        node = nodes[u]
        gallons = miles_bought / mpg
        cost = gallons * node.price
        bought_mi += miles_bought
        total_cost += cost
        stops.append(Stop(candidate=node.candidate, gallons=gallons, cost_usd=cost))

    return FuelPlan(
        stops=stops,
        departure_fill=departure_fill,
        total_cost_usd=total_cost,
        gallons_purchased=bought_mi / mpg,
        gallons_burned=total_mi / mpg,
        gallons_remaining=max(0.0, bought_mi - total_mi) / mpg,
        stop_penalty_usd=stop_penalty_usd,
    )


def _check_reachable(nodes: list[_Node], total_mi: float, range_mi: float) -> None:
    """Fail early, and say where the route runs dry."""
    if not nodes:
        raise PlanningError("No truck stops from the price list lie along this route.")
    if nodes[0].mile > range_mi:
        raise PlanningError(
            f"The first truck stop on this route is {nodes[0].mile:.0f} miles in, "
            f"further than the {range_mi:.0f} mile range allows."
        )
    for a, b in zip(nodes, nodes[1:]):
        if b.mile - a.mile > range_mi:
            raise PlanningError(
                f"There is a {b.mile - a.mile:.0f} mile stretch with no truck stop "
                f"between mile {a.mile:.0f} and mile {b.mile:.0f}, which is more "
                f"than the {range_mi:.0f} mile range."
            )
    if total_mi - nodes[-1].mile > range_mi:
        raise PlanningError(
            f"The last truck stop is {total_mi - nodes[-1].mile:.0f} miles short of "
            f"the destination, which is more than the {range_mi:.0f} mile range."
        )
