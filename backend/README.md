# Geospatial backend foundation

Python 3.9+ library for North Carolina road-network and destination queries.
No frontend, server, hazard scoring, weather API, or synchronization is included.

## Structure

- `survival_geo/roads.py`: drivable OSM MultiDiGraph download/load and GeoPandas edge export.
- `survival_geo/destinations.py`: configurable OSM destination categories.
- `survival_geo/routing.py`: directed routing, replaceable edge costs, GeoJSON results.
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
`attributes['length']`. Future hazard modules can add penalties through this hook or
remove graph edges before routing; no penalties are implemented here. Parallel edges
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
