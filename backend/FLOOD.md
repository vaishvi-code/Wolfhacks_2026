# Official flood-data adapter

The flood package converts supplied official alert polygons into the existing
`Hazard` model and uses the existing risk engine and road graph. USGS water
observations are returned alongside route results as context. This is
decision-support software, not an authoritative emergency warning system.

## Sources and exact data products

| Source | Endpoint | Contribution |
| --- | --- | --- |
| NOAA/NWS active CAP alerts, GeoJSON | `GET https://api.weather.gov/alerts/active?area=NC` or `?point=latitude,longitude` | Flood-related event names, official polygon/multipolygon when provided, severity, certainty, sent/effective/onset/expires/ends, descriptions, instructions, alert IDs, zones, references |
| USGS OGC API v1 latest continuous measurements, GeoJSON | `GET https://api.waterdata.usgs.gov/ogcapi/v1/collections/latest-continuous/items` | Most recent measurement per time series, monitoring-location ID and point, observation time, value, parameter, units, quality metadata |

USGS queries use `bbox=west,south,east,north` for discovery near a location or
`monitoring_location_id=USGS-...` for an explicit site. The default two requests
use `parameter_code=00060` (discharge) and `parameter_code=00065` (gage height),
with `f=json&limit=1000`. Other five-digit parameter codes are configurable.
Discovery is a bounding-box search for locations with measurements, not an
exhaustive site inventory or a hydrologic connection analysis. No separate
monitoring-locations or flood-threshold product is queried.

References: [NWS API documentation](https://www.weather.gov/documentation/services-web-api),
[NWS geolocation guide](https://www.weather.gov/media/documentation/docs/NWS_Geolocation.pdf),
[USGS latest-continuous schema](https://api.waterdata.usgs.gov/ogcapi/v1/collections/latest-continuous/schema?f=html),
[USGS API guide](https://api.waterdata.usgs.gov/docs/ogcapi/).

`NWSClient` sends an identifying User-Agent; use your project's contact/website
in the configured value. `USGSClient` optionally accepts an API key via the
`X-Api-Key` header, never in output provenance. See
[USGS API-key documentation](https://api.waterdata.usgs.gov/docs/ogcapi/keys/).
Both clients follow same-origin pagination, default to at most five pages per
query, and use 5-second connection / 20-second read timeouts per request.
Truncation, malformed pages, or later-page failures produce `partial` status.
There are no automatic retries or scheduled polling.

## Package structure

- `flood/models.py`: `FloodAlert`, `WaterObservation`, `SourceResult`, explicit local fallback.
- `flood/nws.py`, `flood/usgs.py`: HTTP adapters and pure response parsers.
- `flood/transport.py`: bounded HTTP/pagination and failure reporting.
- `flood/freshness.py`: configurable timestamp/expiry checks.
- `flood/adapter.py`: `FloodPolicy` and `prepare_flood_hazards`.
- `flood/routing.py`: flood-aware comparison with provenance and qualified reasons.
- `flood/demo.py`: optional live CLI.
- `tests/fixtures/flood/`, `tests/test_flood.py`: synthetic official-schema responses and mocks.

## Alert footprints vs contextual measurements

Only valid official Polygon/MultiPolygon alert geometry becomes a spatial flood
hazard. It is retained without expansion, interpolation, or fabricated boundaries.
Geometry-less alerts remain in the response with an exclusion reason, zones,
and descriptive information. Zone boundaries are not resolved in this version.
Malformed alert geometry marks the source response partial.

Event names containing `flood` are retained. Test/exercise messages, cancellations,
superseded IDs referenced by updates/cancellations in the same snapshot, expired
alerts, and alerts not yet effective are excluded from routing. Future onset does
not suppress an already effective watch/warning. Missing expiration, missing sent
time, or unknown freshness prevents default routing use.

Each adapted `Hazard` retains the original severity string, official alert ID,
source, timestamps, geometry, event, descriptions/instructions, and fetch time.
`hazard_type` is `flood`; `metadata.evidence_kind` is `official_alert_area`.
The configurable `source_confidence=1.0` is an adapter setting, **not** an
NWS-supplied probability. Official CAP certainty is preserved separately without
inventing a numeric conversion. Default uncertainty penalties remain zero.

USGS values never become routing hazards here, regardless of their magnitude.
They retain both numeric `value` and source `raw_value`, units, time-series/site
IDs, observation/fetch times, point geometry, qualifier, and approval status.
Non-numeric, non-finite, missing-value sentinel, and incomplete measurement records
are reported as parse issues. Gage height is not road flood depth. No gauge buffers,
danger thresholds, interpolated flood polygons, or inferred road closures exist.

## Flood policy

`FloodPolicy` extends the existing `RiskPolicy.level_for` hook. Flood/flash-flood/
coastal-flood/lakeshore-flood warnings default to `HIGH_RISK`; watches, advisories,
and statements default to `CAUTION`. Unknown flood event names default to CAUTION.
This event mapping is a routing preference, not a disaster-severity estimate.

Inherited configurable demo penalties are 250 for CAUTION and 2500 for HIGH_RISK
per intersecting hazard per road edge, in meter-equivalent cost units. They do not
change physical route distance. An alert polygon alone cannot map to IMPASSABLE;
explicit road-closure evidence requires a future adapter. A flood-aware route can
still cross a warning area when that minimizes configured cost.

```python
from survival_geo import RiskLevel
from survival_geo.flood import FloodPolicy

policy = FloodPolicy(
    penalties={RiskLevel.SAFE: 0, RiskLevel.CAUTION: 100,
               RiskLevel.HIGH_RISK: 5000, RiskLevel.IMPASSABLE: 0},
    uncertainty_penalty_scale=0,
)
```

Comparison reasons say **"Road intersects active Flood Warning area"**, followed
by its identifier and the limitation that this does not establish road flooding.
No intersection or no returned alert does not establish road safety. Generic
`SAFE`/`passable` fields retain their existing cost-policy meanings, not physical
safety or confirmed road-access guarantees. Check `flood_data.assessment_status`,
source status, and individual freshness before interpreting the route.

## Freshness and unavailable data

Every parsed record preserves its original source timestamp, fetch timestamp,
source, and expiration where supplied. `SourceResult` separates fetch time from
the latest attempt time. All timestamps are timezone-aware and serialized as ISO.

| Default setting | Alerts | Gauge observations |
| --- | --- | --- |
| Maximum age since fetch | 15 minutes | 15 minutes |
| Maximum observation/issuance age | None; use alert validity window | 2 hours |
| Future-clock tolerance | 2 minutes | 2 minutes |

`freshness(...)` returns `current`, `stale`, `expired`, `unknown`, or
`not_yet_active`. Expiration is inclusive and takes precedence; for NWS the earlier
of `expires` and `ends` applies. Fetching an old gauge reading does not make it
current. A recently re-fetched multi-day alert can remain current until expiry.
The thresholds are application choices, not scientific validity guarantees:

```python
from datetime import timedelta
from survival_geo.flood import FreshnessPolicy

alert_age = FreshnessPolicy(max_fetch_age=timedelta(minutes=10))
gauge_age = FreshnessPolicy(max_fetch_age=timedelta(minutes=10),
                          max_observation_age=timedelta(hours=1))
```

Pass these as `alert_freshness` / `observation_freshness` to the comparison.
Stale alerts are excluded by default and retained visibly in `flood_data.alerts`.
`include_stale=True` is an explicit opt-in to their routing use, still labeled
stale/degraded; expired and unknown records remain excluded. An empty successful
response also ages based on its fetch time.

`SourceResult.status` is `available`, `partial`, or `unavailable`. Empty successful
responses are available with zero records; HTTP errors/timeouts/invalid envelopes
are not silently converted into successful empty responses. A failed source does
not prevent distance routing. Comparison returns a degraded assessment and an
explicit outage notice, even if routes happen to match.

The [offline service](OFFLINE.md) now provides persistent snapshots and explicit
refresh. At the source-adapter level, parsers also accept caller-managed raw local
API payloads and their **original** fetch time:

```python
from survival_geo.flood import NWSClient, parse_nws_alerts, use_local_fallback

# saved_payload, original_fetch_time, original_query belong to the same snapshot.
local = parse_nws_alerts(saved_payload, fetched_at=original_fetch_time,
                        query=original_query, data_origin='local')
latest = NWSClient().fetch(area='NC')
alerts = use_local_fallback(latest, local)
```

This source-level fallback helper is used only on total unavailability, requires matching source/query
scope, and retains unavailable status and old timestamps. Partial live results
are not merged by this helper; the offline service implements an explicit partial
merge policy. Snapshots cannot reveal cancellation or
replacement messages that were never fetched; age checks do not solve that gap.

## Integration and output

```python
from survival_geo import load_road_graph
from survival_geo.flood import NWSClient, USGSClient, compare_flood_routes

origin = (35.7796, -78.6382)
destination = (35.7850, -78.6350)
graph = load_road_graph(*origin, radius_m=5000, graph_path='data/raleigh.graphml')
alerts = NWSClient(user_agent='MyProject/0.1 (your-project-contact)').fetch(area='NC')
gauges = USGSClient().fetch(point=origin, radius_m=25000)
comparison = compare_flood_routes(graph, origin, destination, alerts, gauges)
```

The comparison retains the generic `distance`, `avoided_roads`,
`avoided_hazard_ids`, and `road_risks` fields, and names the other mode
`flood_aware`. Each mode has `{exists, route, reason}`. Routes contain GeoJSON
geometry, node/edge IDs, physical distance, accumulated penalties, and
`flood_hazards_encountered`. `flood_hazards_avoided` includes full hazard provenance.
Road evaluations include source IDs, freshness, and qualified explanations.

`flood_data` contains source status/query scope, alert records and exclusions,
hazards, contextual observations, freshness settings, applicability to graph edges,
evaluation time, and an assessment notice. `current_inputs` means the supplied
inputs passed these checks; it does not promise complete hazard coverage. Empty
or geometry-less alert responses never imply physical safety. Point queries cover
the queried point, so prefer state/area queries covering the entire graph corridor.

To use just the extension point, `prepare_flood_hazards(alerts).hazards` can be
passed to `evaluate_road_risks(road_edges(graph), hazards, FloodPolicy())` or the
generic comparison. Use `compare_flood_routes` to retain source-outage context and
flood-specific explanations in the frontend-facing result.

## Tests and demos

From `backend/` with the existing environment active:

```sh
python -m pip install -r requirements-dev.txt
python -m pytest -q                     # full suite, all API calls mocked
python -m pytest tests/test_flood.py -q  # adapter-specific tests
python -m survival_geo.demo             # original guaranteed synthetic demo
```

Optional live demo, Raleigh by default:

```sh
python -m survival_geo.flood.demo --graph-path data/raleigh.graphml
python -m survival_geo.flood.demo --latitude 35.5951 --longitude -82.5515 \
  --destination-latitude 35.6000 --destination-longitude -82.5500 \
  --radius-m 5000 --area NC --graph-path data/asheville.graphml
python -m survival_geo.flood.demo --sources-only
```

Optional environment settings: `NWS_USER_AGENT` and `USGS_API_KEY`.
Without a supplied destination, the demo selects a graph node solely to exercise
routing; it is **not** a recommended safe destination. If the network/OSM graph
is unavailable, source results are retained and routing reports its failure.
When no mapped active hazard intersects the graph, the comparison explicitly says
**"No applicable active flood hazard was returned."** It does not invent an event.
The synthetic demo remains the reliable way to demonstrate route avoidance when
no real flood is active locally. Fixtures are clearly synthetic and never used
as fallback by the live demo.

Limitations remain: whole-edge penalties, approximate road snapping, no bridge or
elevation analysis, no exact flood extent/depth, no official road-closure feed,
no hazard coverage guarantee, and no automatic reconciliation across snapshots.
Live data may be delayed or revised; USGS quality/approval metadata is preserved.
