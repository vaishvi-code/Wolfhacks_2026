# Geospatial backend and generic risk routing

Python 3.9+ library for North Carolina road-network and destination queries.
Includes source-independent hazard mapping and configurable routing policy.
No frontend, server, live hazard APIs, disaster-specific scientific model, or
synchronization is included.

## Structure

- `survival_geo/roads.py`: drivable OSM MultiDiGraph download/load and GeoPandas edge export.
- `survival_geo/destinations.py`: configurable OSM destination categories.
- `survival_geo/routing.py`: directed routing, replaceable edge costs, GeoJSON results.
- `survival_geo/hazards.py`: validated, source-independent hazard records.
- `survival_geo/risk.py`: GeoPandas spatial matching, risk policy, road-risk results.
- `survival_geo/risk_routing.py`: cost adapter and route comparison.
- `survival_geo/destination_safety.py`: destination exclusion with reasons.
- `survival_geo/demo.py`: synthetic graph and offline route comparison.
- `survival_geo/errors.py`, `validation.py`: shared errors and input checks.
- `tests/`: synthetic graphs and mocked OSM requests; no live downloads.

## Setup and tests

From the repository root:

```sh
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

For integration from another Python module, install this package with
`python -m pip install -e ./backend` from the repository root (with the virtual
environment active).

## Minimal example

Run Python from `backend/` after setup:

```python
import json
from survival_geo import load_road_graph, discover_destinations, build_route, road_edges

origin = (35.7796, -78.6382)  # Raleigh
roads = load_road_graph(*origin, radius_m=5000, graph_path='data/raleigh.graphml')
edges = road_edges(roads)  # GeoDataFrame: (u, v, key), osmid, length, geometry
hospitals = discover_destinations(*origin, radius_m=3000,
                                 categories={'hospital': {'amenity': 'hospital'}})
target = hospitals[0]
route = build_route(roads, origin, (target['latitude'], target['longitude']))
print(json.dumps(route))
```

Initial downloads require internet. An existing GraphML path loads locally without
refreshing; destination discovery requires an OSM request. This is explicit file
persistence, not offline synchronization. Downloads retain disconnected components
and road directionality. The caller should try other candidates when one has no route.

Inputs use latitude/longitude; GeoJSON uses longitude/latitude in EPSG:4326.
Lengths and `total_distance_m` are meters. Results include route node IDs, selected
edge IDs, GeoJSON LineString geometry, and requested and snapped endpoints. Distance
excludes travel from requested points to snapped nodes. Same-node routes return zero
meters and a two-coordinate degenerate LineString. Endpoint snapping defaults to a
maximum of 1000 meters and can be tightened by callers.

Coverage uses the download bounding box (stored in GraphML), or node bounds for an
externally supplied graph. It is an approximate extent, not a road service-area
polygon. Only unprojected EPSG:4326 OSMnx MultiDiGraphs are accepted.

`build_route(..., edge_cost=policy)` accepts a function `(u, v, key, attributes)`
returning a nonnegative finite cost, or `None` to exclude an edge. The default uses
`attributes['length']`. The generic risk adapter adds policy penalties through this
hook and returns `None` for impassable edges. Parallel edges
are selected using policy cost and route geometry/distance use that exact edge.

Catch `EmptyOSMResults`, `DataAccessError`, `LocationOutsideGraph`, and
`NoRouteAvailable` from `survival_geo.errors`. Invalid inputs raise `ValueError`.
OSM candidates do not establish facility safety, opening hours, capacity, official
shelter activation, or reachability. Shelter tags can include ordinary public shelters.
Category tag filters use OSMnx's OR semantics; pass a custom category mapping to extend
or replace defaults. The library accepts global coordinates rather than enforcing a
North Carolina boundary.

OSM data is © OpenStreetMap contributors (ODbL); include attribution in the eventual
frontend. API reference: https://osmnx.readthedocs.io/en/stable/user-reference.html

## Hazard model

`Hazard` is a dataclass with:

| Field | Contract |
| --- | --- |
| `id` | Nonempty string, unique within the supplied evaluation snapshot |
| `hazard_type` | Extensible string: e.g. `flood`, `hurricane`, `heat`, `wildfire`, `road_closure`, `user_reported` |
| `geometry` | Valid, nonempty 2D Shapely Point, LineString, Polygon, or MultiPolygon in EPSG:4326 |
| `severity` | Supplied policy label; not a universal physical severity scale |
| `timestamp` | Optional timezone-aware `datetime`, representing the source observation |
| `source` | Nonempty source identifier |
| `confidence` | Supplied finite number from 0 to 1; not calibrated by this library |
| `metadata` | JSON-compatible mapping for source-specific details |

`hazard.to_dict()` returns a GeoJSON-compatible dictionary with an ISO timestamp.
No hazard-type enum restricts future modules. Invalid geometry, duplicate IDs,
unknown severity labels, and malformed input raise `ValueError`. Empty `[]` means
no supplied hazards; `None` is rejected to prevent accidentally treating missing
input as an explicitly empty snapshot. Source modules must resolve duplicate
observations and identifiers before evaluation.

## Road-risk model and spatial analysis

```python
from survival_geo import evaluate_road_risks, road_edges

risks = evaluate_road_risks(road_edges(roads), hazards)
first_risk = next(iter(risks.values()))
print(first_risk.to_dict())
```

The result maps `(u, v, key)` to a `RoadRisk` for **every** evaluated edge,
including unaffected edges. Each result exposes `edge_id`, `risk_level`,
`hazard_penalty`, `uncertainty_penalty`, combined `penalty`, `passable`, `reasons`,
`hazard_ids`, `confidence`, and timezone-aware `evaluated_at`.

Road GeoDataFrames must declare their CRS and have a unique `(u, v, key)` index.
Hazards are transformed to that CRS for an indexed GeoPandas spatial join.
Intersections include boundary touches. Matching follows
[GeoPandas spatial-join semantics](https://geopandas.org/en/stable/docs/user_guide/mergingdata.html).
Point and line hazards have no implicit radius: source modules must supply an
affected footprint (or buffer in an appropriate metric CRS and transform back).
No graph or input GeoDataFrame is modified. Both road directions and parallel
edges are evaluated independently by their own geometry.

## Configurable policy, not scientific risk estimation

Default **demo policy**:

| Supplied severity | Road category | Per-hazard penalty | Passable |
| --- | --- | ---: | --- |
| `none` | SAFE | 0 | Yes |
| `low`, `moderate` | CAUTION | 250 | Yes |
| `high` | HIGH_RISK | 2500 | Yes |
| `critical` | IMPASSABLE | 0 (excluded instead) | No |

These are arbitrary routing preferences, **not scientifically validated disaster
thresholds or danger estimates**. Penalties are meter-equivalent cost units for
distance-based routing; they do not increase the reported physical distance.

For overlapping hazards, the highest category determines passability, penalties
are summed once per distinct hazard per edge, and reported confidence is the
minimum supplied confidence. Reasons and IDs retain every intersecting hazard.
An IMPASSABLE edge remains excluded even at low confidence. The zero default
penalty for IMPASSABLE does not indicate safety: check category and passability.

Optional uncertainty cost is `uncertainty_penalty_scale * (1 - confidence)` per
intersecting non-SAFE hazard. Its default scale is zero. This is also a replaceable
policy choice, not a probability model. Unaffected edges have zero penalties and
`confidence=None`; SAFE means no mapped risk from the supplied snapshot, not
verified real-world safety or complete hazard coverage.

```python
from survival_geo import RiskLevel, RiskPolicy

policy = RiskPolicy(
    severity_levels={
        'none': RiskLevel.SAFE, 'low': RiskLevel.CAUTION,
        'moderate': RiskLevel.CAUTION, 'high': RiskLevel.HIGH_RISK,
        'critical': RiskLevel.IMPASSABLE,
    },
    penalties={
        RiskLevel.SAFE: 0, RiskLevel.CAUTION: 100,
        RiskLevel.HIGH_RISK: 5000, RiskLevel.IMPASSABLE: 0,
    },
    uncertainty_penalty_scale=200,
)
```

## Distance vs risk-aware routing

`build_route` retains its original distance-only behavior. `build_risk_route`
uses `base_travel_cost + hazard_penalty + uncertainty_penalty`; excluded edges
return `None` through the existing cost hook. Default base cost is edge length.
An optional `base_travel_cost` callable must use compatible units and may also
exclude edges. Missing road-risk evaluations raise an error rather than assuming
SAFE. Recompute evaluations after graph geometry or hazard/policy changes.

`compare_routes(graph, origin, destination, hazards, policy=None)` evaluates one
snapshot and returns:

- `distance` and `risk_aware`: `{exists, route, reason}`. Each present route has
  the existing distance/geometry/endpoints plus accumulated hazard, uncertainty,
  and total risk penalties, passability, hazard IDs, and selected road risks.
- `avoided_roads`: affected edges on the distance route absent from the risk route,
  with their risk records and reasons. Exact `(u, v, key)` identities are used.
- `avoided_hazard_ids`: hazards encountered on the distance route but absent from
  the risk route. Avoiding one road does not imply avoiding its hazard elsewhere.
- `road_risks`: serialized evaluations for the complete road network.

When no route exists for a mode, `route=None`, `exists=False`, and `reason`
explains it. Avoidance fields are `None` unless both routes exist; otherwise
they are lists (possibly empty). Input/coverage errors still propagate. A
distance-only route may cross IMPASSABLE roads and reports `passable=False`.
A risk-aware route can still cross HIGH_RISK or CAUTION roads if that minimizes
configured cost; its existence is not a claim that a hazard-free route exists.

## Offline synthetic demonstration

From `backend/`, with the environment active:

```sh
python -m survival_geo.demo
```

This prints the full JSON comparison, including geometries, without any download.
The synthetic graph uses assigned demo lengths:

```text
1 --450m--> 2 --450m--> 3    900m: intersects demo-closure
1 --550m--> 4 --550m--> 3   1100m: no supplied hazard intersection
```

The distance route is `[1, 2, 3]`. The risk-aware route is `[1, 4, 3]`, avoids
two impassable edges and `demo-closure`, and has zero accumulated penalty.

Programmatic usage (also entirely offline):

```python
from survival_geo import compare_routes, filter_destinations
from survival_geo.demo import demo_scenario

graph, origin, destination, hazards = demo_scenario()
comparison = compare_routes(graph, origin, destination, hazards)
candidates = [
    {'name': 'Inside closure', 'latitude': 35.770, 'longitude': -78.635},
    {'name': 'Unaffected candidate', 'latitude': 35.773, 'longitude': -78.635},
]
filtered = filter_destinations(candidates, hazards)
print(comparison['risk_aware']['route']['total_distance_m'])  # 1100
print(filtered['excluded'][0]['hazard_ids'])  # ['demo-closure']
```

## Destination filtering and future modules

`filter_destinations` accepts the existing discovery dictionaries. It uses full
GeoJSON footprints when present; otherwise latitude/longitude becomes a point.
By default, HIGH_RISK and IMPASSABLE intersections exclude a candidate, including
boundary touches. `unsafe_levels` can also include CAUTION. Returned `accepted`
records preserve the candidates; each `excluded` item preserves the original
destination plus hazard IDs, reasons, and evaluation time. Acceptance means only
that this geometric exclusion rule did not reject it; reachability, availability,
and ranking remain separate concerns.

Future flood/hurricane/heat adapters should translate their input to `Hazard`
records, supply footprints and severity labels, and provide a `RiskPolicy`
mapping. Override `RiskPolicy.level_for(hazard)` to inspect hazard type or metadata
without changing the routing engine. For more advanced per-road analysis, a
module can produce a complete mapping of validated `RoadRisk` records and pass
it directly to `build_risk_route` or use `risk_edge_cost` with `build_route`.

Current limitations: whole edges receive penalties even for small intersections;
splitting edges changes aggregate costs; correlated overlapping hazards are not
deduplicated beyond unique IDs; bridges/tunnels and elevations are ignored; no
implicit distance buffers, confidence calibration, timestamp expiry, or stale-data
policy is applied. Timestamp presence does not affect routing. Snapping and
off-road connector limitations from the original backend still apply. This layer
interprets supplied hazard information; it does not estimate real disaster danger.
