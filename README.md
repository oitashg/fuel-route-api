# Fuel Route API

Takes a start and a finish inside the USA, routes the drive, and works out the
cheapest places to buy fuel along the way for a truck that does 10 mpg and can
go 500 miles on a tank.

> Django 6.1 + Django REST Framework. Routing from OSRM and geocoding from
> Nominatim, both free and keyless. No PostGIS — the geometry is plain
> arithmetic, so it runs on stock SQLite.

Planning one trip costs **one call to the routing service**, plus at most two
geocoding calls for the two place names. Everything else — 6,626 truck stops,
their prices and their coordinates — is already in the database.

```
POST /api/v1/route/   {"start": "Dallas, TX", "finish": "Chicago, IL"}
```

```
966 miles, 2 fuel stops, $277.10 of fuel, 1 upstream call, ~30 ms once warm.
```

---

## What it returns

- The route geometry as GeoJSON, ready to draw, plus a rendered Leaflet map at
  `/map/<id>/` for anyone who would rather look than read JSON.
- Every fuel stop in order: the truck stop, its town, its price, how many
  gallons to buy there, what that costs, and how far along the route it is.
- The total fuel bill for the drive, with the gallons it is made of.

## Where each part of the brief lives

| Requirement | Where |
|---|---|
| API takes a start and finish in the USA | `fuel/views.py` `plan`, `fuel/serializers.py` |
| Map of the route | `fuel/services/trip.py` `_route_payload`, `templates/fuel/map.html` |
| Optimal fuel stops by price | `fuel/services/planner.py` `plan_fuel_stops` |
| Multiple fill-ups within a 500-mile range | same, and `_check_reachable` |
| Total spent at 10 mpg | `fuel/services/planner.py`, reported in `fuel/services/trip.py` |
| Prices from the supplied file | `fuel/management/commands/load_fuel_prices.py` |
| Free map/routing API, called as little as possible | `fuel/services/geo.py` |

---

## The API

| Method | Path | What it does |
|---|---|---|
| `POST` | `/api/v1/route/` | Plan a trip. Returns the route, the stops and the cost. |
| `GET` | `/api/v1/routes/<id>/` | The same plan again, from storage. |
| `GET` | `/api/v1/routes/` | The ten most recent plans. |
| `GET` | `/api/v1/assumptions/` | The vehicle and the rules the planner works to. |
| `GET` | `/api/v1/health/` | Liveness, and how much price data is loaded. |
| `GET` | `/` | A form for trying it without a REST client. |
| `GET` | `/map/<id>/` | The route and its fuel stops drawn on a map. |

### Request

```json
{
  "start": "Dallas, TX",
  "finish": "Chicago, IL",
  "geometry": "simplified",
  "stop_penalty_usd": 10
}
```

`start` and `finish` take anything Nominatim understands — a town and state, a
full street address — or a bare `"32.7767,-96.7970"` pair, which skips the
geocoder entirely. `geometry` is `simplified` (the default), `full`, or `none`.
`stop_penalty_usd` is optional and explained [below](#3-decide-where-to-buy);
send `0` for the cheapest fuel regardless of how many stops that takes.

### Response

```json
{
  "id": "edb1c869-2b07-42e1-b2aa-273c2442e3c2",
  "start":  {"query": "Dallas, TX",  "name": "Dallas, TX",  "lat": 32.776, "lon": -96.797},
  "finish": {"query": "Chicago, IL", "name": "Chicago, IL", "lat": 41.876, "lon": -87.624},
  "route": {
    "distance_mi": 966.6,
    "duration_h": 17.1,
    "geometry": {"type": "LineString", "coordinates": [[-96.79, 32.78], "..."]}
  },
  "fuel": {
    "total_cost_usd": 277.1,
    "departure_tank": {
      "gallons": 16.38, "cost_usd": 46.15, "price_per_gal": 2.817,
      "miles_to_first_stop": 163.8, "priced_at": "EXTRA MILE TRUCK STOP"
    },
    "gallons_purchased": 96.66,
    "gallons_burned": 96.66,
    "gallons_in_tank_on_arrival": 0.0,
    "average_price_per_gal": 2.867,
    "stop_count": 2,
    "stop_penalty_usd": 10.0,
    "stops": [
      {
        "sequence": 1,
        "opis_id": 68256,
        "name": "EXTRA MILE TRUCK STOP",
        "address": "I-30 EXIT 208",
        "city": "Hooks", "state": "TX",
        "lat": 33.4775, "lon": -94.2692,
        "price_per_gal": 2.817,
        "gallons": 50.0,
        "cost_usd": 140.87,
        "route_mile": 163.8,
        "miles_to_next_stop": 351.0,
        "detour_mi": 0.5
      },
      {
        "sequence": 2,
        "opis_id": 66643,
        "name": "DEERFIELD TRAVEL CENTER",
        "address": "I-55, EXIT 8",
        "city": "Steele", "state": "MO",
        "price_per_gal": 2.976,
        "gallons": 30.28,
        "cost_usd": 90.09,
        "route_mile": 514.8,
        "miles_to_next_stop": 451.8,
        "detour_mi": 1.7,
        "...": "..."
      }
    ]
  },
  "vehicle": {"mpg": 10.0, "range_mi": 500.0, "tank_gallons": 50.0},
  "assumptions": ["..."],
  "meta": {
    "routing_api_calls": 1,
    "geocoding_api_calls": 2,
    "route_from_cache": false,
    "corridor_mi": 15.0,
    "stations_on_route": 222,
    "route_points": 9173,
    "elapsed_ms": 2385.0
  },
  "map_url": "http://localhost:8000/map/edb1c869-2b07-42e1-b2aa-273c2442e3c2/"
}
```

`meta` is there so the cost of a request is visible rather than claimed.

Errors come back as `{"detail": "..."}`: **400** for a place that cannot be
geocoded or an upstream outage, **422** when the route is fine but the price
list cannot fuel it (the message names the gap), **500** for anything
unexpected.

---

## How the fuel stops are chosen

Three steps, none of which touch the network.

**1. Find the truck stops that are actually on the route.** OSRM returns the
drive as roughly 9,000 points for Dallas to Chicago, 34,000 for Los Angeles to
New York. Measuring 6,626 truck stops against every one of them is far too slow
for a request, so the route points go into a grid of lat/lon cells and each
truck stop only looks at its own cell and the eight around it. The cells are
sized from the corridor width so that search is exhaustive and not merely
likely. The line is also thinned to about one point per mile first, which costs
at most half a mile of accuracy against a corridor measured in tens of miles.
Dallas to Chicago: 222 truck stops found, in 19 ms.

**2. Thin them.** The price file gives a town, not a street corner, so every
truck stop in a town lands on the same point of the route. Only the cheapest
one there can ever be the right answer, so the rest are dropped.

**3. Decide where to buy.** If price is the only thing that matters, this is
the textbook minimum-cost refuelling problem and a greedy solves it exactly:
at each stop, buy just enough to reach the next cheaper one, or fill up if
nothing in reach is cheaper. I started there, and it is right about the money
and wrong about the driving. On Dallas to Chicago it made 5 stops, one of them
to buy a single gallon because the stop ten miles on was a few cents dearer.
Los Angeles to New York took 16 stops, seven of them under eight gallons. Nobody
drives like that.

So each stop also carries a fixed penalty, `stop_penalty_usd`, $10 by default:
roughly a quarter of an hour of a driver's time to pull off, fuel and get back
on. It only decides where to stop. It is never added to the fuel bill, and
sending `0` gives back the pure price optimum.

| Route | Cheapest fuel (`0`) | With the $10 default |
|---|---|---|
| Dallas → Chicago | 5 stops, $273.81 | 2 stops, $277.10 |
| Los Angeles → New York | 16 stops, $857.88 | 6 stops, $867.90 |
| Miami → Seattle | 18 stops, $996.77 | 7 stops, $1,009.57 |

Ten fewer stops on a coast-to-coast run for $10 of fuel, and no purchase under
30 gallons on any of the three.

Once a stop costs something the greedy is no longer optimal, so the planner is
a dynamic program instead. It uses one property of optimal plans (Khuller,
Malekian and Mestre, *To Fill or Not to Fill*, 2007): leaving a stop for the
next one, the truck either fills the tank or buys just enough to arrive empty.
Anything in between can be moved one way or the other without costing more. So
the fuel on arrival at a stop is either zero or "a full tank minus the drive
from some earlier stop", which leaves a few dozen states per stop instead of a
continuum. Filling up costs the same whichever stop comes next, and arriving
empty is a prefix minimum over the states sorted by fuel, so the whole thing
runs in a few milliseconds even coast to coast.

The destination is a node that can only be reached empty, which is what stops
the plan from buying fuel it does not need. The result is optimal, not a
heuristic that usually looks sensible, and `fuel/tests/test_planner.py` holds
it to that: 500 random routes per run, with and without a stop penalty, each
one also solved by exhaustive search, with the two answers required to match.

### The one wrinkle: the tank at the start

The price file does not cover every town. Leaving Los Angeles, the first truck
stop in it is 225 miles away, so the truck cannot have started empty. It has to
leave with fuel in the tank, and that fuel has to be paid for somewhere.

It is priced at the first stop's rate, which is the nearest thing to a market
price that is actually known, and reported separately as `departure_tank` so it
is never confused with what was bought at the stop itself. That also means a
distant first stop is not free, so the planner has a real decision to make
about where to fill first, and it tries every stop within a tank of the origin.
On Dallas to Chicago it passes the cheapest stop of the trip, six miles out,
because filling there would mean two more stops later.

The upshot is that `gallons_purchased` equals `distance / 10` exactly, so the
total is the real cost of the whole drive and not a partial one.

---

## Keeping the upstream calls down

| | |
|---|---|
| Routing | One OSRM call per trip, cached on the two endpoints rounded to four decimals. A repeat of the same trip costs none. |
| Geocoding | One Nominatim call per place name, cached; none at all if coordinates are passed instead. |
| Fuel stops | None, ever. Prices and coordinates are imported once and read from the database. |

The geometry comes back as polyline6 and is decoded locally, which is about a
quarter of the bytes of the equivalent GeoJSON on a cross-country route. On the
way out it is thinned with Ramer–Douglas–Peucker to a tolerance of roughly 150
metres: invisible on a map of a 3,000-mile drive, and about a megabyte lighter.

Measured on a MacBook, cold cache, Miami to Seattle (3,302 miles):

| Stage | Time |
|---|---|
| Geocode both ends + route | ~3 s (network) |
| Load 6,626 truck stops into memory | 46 ms, once per process |
| Find the 421 stops on the route | 19 ms |
| Choose the fuel stops | 4 ms |
| Whole request, route already cached | ~90 ms |

---

## The price data

The supplied CSV has 8,151 rows but only 6,738 truck stops: a stop appears once
per price sample. They are folded onto the OPIS id, and `price` is the mean of
the samples with `price_samples` recording how many there were. Where the same
stop is spelled several ways across its rows ("PILOT #1243" and "PILOT TRAVEL
CENTER #1243"), the fullest spelling wins. 620 rows are Canadian, and since the
brief puts both ends of the trip in the USA they are dropped. That leaves
6,626.

The file has no coordinates, and putting 6,626 truck stops through Nominatim at
its one-request-per-second policy would take two hours. A bundled table of
~30,000 US cities resolves all but six of them offline; those six were looked
up once and written to `fuel/data/extra_places.csv`. So the import needs no
network at all and every truck stop ends up located:

```
Located 6626: 6626 from the bundled table, 0 from Nominatim.
```

Town-centre coordinates are as precise as the file allows, which is why a stop
can show a 13-mile detour when it is really beside the interstate. The corridor
is 15 miles wide to accommodate that, and `detour_mi` is reported per stop so
the error is visible rather than hidden.

---

## Assumptions

- 10 miles per gallon, 500 miles of range, so a 50-gallon tank.
- The fuel already in the tank at the origin is paid for, at the price of the
  first stop, and reported separately as the departure tank.
- The truck never buys more than it needs, so the total is what the drive
  actually costs in fuel.
- Each stop is treated as costing $10 of driver time when choosing where to
  stop. That is not money spent and is not in the total.
- A truck stop is "on the route" if it is within 15 miles of it. Pulling off
  the highway and back on is not added to the distance driven.
- Prices are whatever the supplied file says. It carries no date and no fuel
  grade, so they are taken as current diesel prices.
- If nothing can be found within 15 miles the corridor widens to 60 once before
  the request fails, which covers the emptier stretches of the west.

`GET /api/v1/assumptions/` returns this list, and the map page prints it under
the stop table.

---

## Running it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python manage.py migrate
python manage.py load_fuel_prices          # 6,626 truck stops
python manage.py geocode_stations          # offline, ~10 seconds
python manage.py runserver
```

Then open <http://127.0.0.1:8000/>, or:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/route/ \
  -H 'Content-Type: application/json' \
  -d '{"start": "Dallas, TX", "finish": "Chicago, IL"}'
```

`docs/fuel-route-api.postman_collection.json` imports into Postman with the
same requests ready to send.

Settings worth knowing about, all overridable by environment variable (see
`.env.example`): `VEHICLE_MPG`, `VEHICLE_RANGE_MI`, `STOP_PENALTY_USD`, `CORRIDOR_MI`,
`CORRIDOR_FALLBACK_MI`, `OSRM_URL`, `NOMINATIM_URL`. SQLite by default;
`DATABASE_URL` switches it to Postgres.

A note on the public OSRM and Nominatim servers: they are demo instances with
rate limits and no uptime promise. For anything beyond a demo, point `OSRM_URL`
and `NOMINATIM_URL` at your own.

The map page draws its basemap from Esri rather than `tile.openstreetmap.org`.
OSM's tile servers run on donations and refuse traffic they do not recognise,
returning an "Access blocked" image in place of every tile, and Carto's free
tiles now stamp "API key required" across themselves. Esri's are keyless and
only ask for attribution. `MAP_TILE_URL` and `MAP_TILE_ATTRIBUTION` change it
if you have a provider of your own. The tiles are only the picture behind the
route -- no part of the API depends on them.

## Tests

```bash
python manage.py test
```

74 tests, no network access: the planner against exhaustive search, the
corridor search, the geometry helpers (including the reference polyline from
Google's specification), the import command, and the endpoints with the two
upstream services mocked.

## Known limits

- Truck stops are placed at the centre of their town. Street-level geocoding
  would tighten both the corridor and the detour figures.
- The detour to a stop is reported but not added to the miles driven. Doing it
  properly means asking OSRM for the detour, which is one extra call per
  candidate stop and so defeats the point of the exercise.
- One price per truck stop. A real feed would be dated and the planner would
  want to use the price expected at the time of arrival.
- No authentication or rate limiting. Neither was asked for, and there is
  nothing here worth protecting.
