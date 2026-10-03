# Heat evidence and relief destinations

This extension uses the existing NWS CAP parser/client, normalized `Hazard`,
`HazardBatch`, spatial risk engine, route comparison, destination filtering,
`RefreshService`, and schema-2 `Snapshot`. It adds no routing or persistence model.
All demo locations, geometries, alerts, and the government designation are synthetic.

## Official sources and scope

- [NWS API documentation](https://www.weather.gov/documentation/services-web-api):
  `https://api.weather.gov/alerts/active?area=NC` and optional
  `https://api.weather.gov/stations/KRDU/observations/latest`.
- [NWS heat products](https://www.weather.gov/safety/heat-ww): accepted names are
  Excessive Heat Warning/Watch, Extreme Heat Warning/Watch, and Heat Advisory.
  Matching is exact after whitespace/case normalization. Unrelated alerts do not
  become heat hazards. Both historical and current event terminology are supported.
- Official station temperature, relative humidity, and heatIndex retain their native
  `unitCode`, nulls, quality-control codes, observation time and original fetch time.
  No heat index, apparent temperature, thresholds, forecast, buffer or heat-dose
  model is calculated. A configured station is context only, not route-wide weather.

Research on October 3, 2026 did not verify a reliable statewide structured cooling
center feed with current designations. [NC DHHS heat data](https://www.dph.ncdhhs.gov/programs/epidemiology/occupational-and-environmental-epidemiology/climate-and-your-health/extreme-heat/nc-heat-health-data-and-reports)
concerns heat/health surveillance, not an operational center directory.
[Cumberland County](https://www.co.cumberland.nc.us/emergencyservices/cooling-warming-centers)
and [Guilford County](https://www.guilfordcountync.gov/government/countywide-programs-and-initiatives/continuum-care/summer-cooling-stations)
publish local cooling information; no stable structured feed and activation contract
was verified for automatic ingestion here. This does not establish that no such feed
exists. No HTML scraping or countywide/statewide center assumptions are implemented.

`ingest_official_centers` is the integration boundary for future verified government
records. Callers supply source ID, authority, source URL, original fetch time, and
records containing stable ID, coordinates, explicit `designated_cooling_center=True`,
`observed_at`, optional `expires_at`, name/categories/geometry. The evaluator also
requires `trusted_sources={source_id: source_url}` configured by a trusted integration.
It does not authenticate arbitrary caller claims or verify a URL's ownership.
Do not build this trust configuration from user/OSM input.

## Normalization and policy

Heat alerts retain canonical `nws:<CAP ID>` IDs, polygon, issuance/effective/onset/end
and expiry times, official event/severity, description/instructions, urgency/certainty,
source endpoint, source status, confidence basis, and freshness in the existing Hazard.
Missing/invalid geometry stays in batch context. No zones or polygons are invented.
Shared CAP lifecycle handling plus explicit cache withdrawal handles updates/cancels;
duplicate IDs are counted once with an issue. Malformed rows produce partial coverage.

`HeatPolicy` maps warnings to HIGH_RISK and watches/advisories to CAUTION. Inherited
configurable penalties default to 2500 and 250 meter-equivalent cost units per
intersecting edge/hazard. These are application preferences, not medically calibrated
limits. Heat does not block roads by default. An IMPASSABLE event override requires
an explicit `blocking_reason` documenting external evidence/policy. No external
closure evidence is supplied automatically. Use the same custom policy when adapting
and routing. Other hazard types retain their existing normalized severity mappings.

The existing engine sums distinct hazard contributions and uses its existing
passability rules. Heat + flood and heat + hurricane regression tests demonstrate
additive penalties and alternate routes. No hazard-specific pathfinder exists.

`route_exposure(graph, route)` can annotate any unified route result. Candidate
reports include baseline/safer exposure by hazard type: original contributions,
IDs/events/official severity/freshness, active affected-edge count and distance.
Distance means the full graph length of intersecting selected edges, not the clipped
portion inside a polygon. Multiple heat alerts count one selected edge once for heat;
the same edge can count under both heat and flood. Missing lengths produce null and
`distance_complete=False`. Stale/informational intersections remain in evidence with
zero active count/distance. Weather context is separate and never incurs route cost.

## Relief discovery, classification and ranking

`discover_relief_candidates` wraps existing OSM discovery with configurable categories:
cooling-centre tags, libraries, community centres, town halls/public facilities,
shelters and hospitals/clinics. Existing category matching uses OR predicates.
Broad building tags are deliberately not defaults: a building is not proof of public
access. Empty/failed OSM results return unavailable discovery with issues.

OSM candidates always remain `potential_heat_relief_location`, even with a cooling
centre tag or an asserted official flag. `official_cooling_center` requires matching
trusted provenance, explicit designation and current evidence. Stale, expired,
untrusted or absent designations cannot receive the official ranking preference.
Designation is not verification of opening hours, air conditioning, capacity,
accessibility, acceptance or safety; those claims remain unknown in output.

`evaluate_relief_candidates` uses a generic DataState and routes every valid candidate
through `route_with_state`. It evaluates the full candidate footprint using existing
`filter_destinations`. Default HIGH_RISK/IMPASSABLE destination intersections exclude
a candidate; this conservative default also excludes locations inside a heat warning.
A widespread warning may therefore yield no eligible centers. Integrations can
explicitly configure `ReliefPolicy.unsafe_levels` when supported by local operational
evidence. Route risk penalties remain independently applied. Routing points must be
inside supplied footprints; discovered OSM representative points satisfy this.

Default deterministic order, after excluding unreachable/hazard-invalid candidates:
1. Current, trusted official designation.
2. Lower safer-route total multi-hazard risk penalty.
3. Lower summed affected-edge distance across hazard types.
4. Shorter safer-route distance.
5. Lexicographic stable destination ID.

`ReliefPolicy(ranking=(...))` can reorder/subset the first four named criteria:
`official`, `risk_penalty`, `affected_distance`, `distance`. ID is always the final
tie-breaker. Official-first is an explicit preference and can outweigh route-risk
ranking among eligible candidates; configure risk-first where appropriate.
No medical personalization, LLM, or operational safety guarantee is provided.
Results include ranking values, decisive reason code versus the next candidate,
exclusions, geometry/provenance/classification, hazard intersections, both route
comparisons, weather context, source freshness/coverage and warnings. Duplicate
candidate IDs exclude all conflicting entries. Optional names/categories may be absent.

## Refresh and offline usage

```python
from survival_geo.heat import registered_heat_adapters, WeatherClient

# Merge into the existing generic registry alongside flood and hurricane entries.
adapters.update(registered_heat_adapters(
    area='NC', observation_client=WeatherClient('KRDU')))
# RefreshService(store, coverage, adapters=adapters).refresh(now=...)
```

Alerts and optional weather are independent adapter entries. Generic partial-failure
merging retains cached heat without erasing other hazard sources. Complete successful
empty refreshes retire previous heat alerts. All-failed refreshes retain prior bytes.

The existing freshness policy supplies 15-minute fetch age, 2-minute future-clock
tolerance, and a 2-hour observation-age limit for weather. Original timestamps are
never rejuvenated. Expired/stale/unknown heat is non-authoritative in unified routing;
missing alert coverage is not declared safe. Custom alert freshness can be passed to
the adapter; weather freshness can be configured on WeatherClient.

Heat and weather serialize through schema-2 snapshots. `relief_offline(service,
graph_path, origin, candidates, now=..., trusted_sources=...)` loads existing local
GraphML with downloads disabled and calls `load_offline`, never refresh or discovery.
Candidates are ordinary destination dictionaries: persist/read them as local JSON
alongside the graph. The demo exercises this. Candidate designations are reevaluated
at offline evaluation time. There is no separate HeatSnapshot or automatic OSM cache.

## Run and verified results

From `backend`:

```sh
.venv/bin/python -m pytest -q
.venv/bin/python -m survival_geo.heat.demo
.venv/bin/python -m survival_geo.heat.live   # optional read-only internet check
```

The deterministic demo starts at node 1. Cooling Center A is a synthetic official-style
fixture; Library B is a synthetic OSM-style potential location. A's baseline is
`[1,2,3]`, 900 m, with heat + flood on edge `(2,3,0)` (450 m each, 5000 combined
penalty). Its safer route is `[1,4,3]`, 1100 m, zero mapped penalty, one affected edge
avoided. B's baseline and safer route are `[1,4,5]`, 1150 m, zero mapped penalty.
A is selected by the official-first rule. Schema 2 offline replay selects A with
zero external/network/offline-adapter calls; tests patch network entry points to fail.

Live verification on October 3, 2026 at 22:56:25 UTC returned available NWS NC data,
0 heat alerts, 0 alert polygons, and 0 mapped hazards. KRDU observation at 22:30 UTC,
fetched at 22:56:26 UTC, reported 18 `wmoUnit:degC`, 93.884545007364
`wmoUnit:percent` relative humidity, and null heatIndex (`wmoUnit:degC`). All supplied
quality-control codes were `V`; weather context was current. No live relief search
was performed. Initial sandbox requests failed; the authorized external retry succeeded.

Limitations/deferred work: verified government center feed/activation and hours,
official zone geometry resolution, station discovery/interpolation, forecast context,
clipped route exposure, accessibility/capacity/indoor conditions, real-time closures,
and production-scale multi-destination optimization. Each candidate currently invokes
the existing route comparison independently. The environment emits the existing
urllib3/LibreSSL compatibility warning; no dependency changes were made.
