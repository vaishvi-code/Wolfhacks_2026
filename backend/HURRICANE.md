# Hurricane and tropical cyclone adapter

The adapter produces the existing `Hazard` and `HazardBatch` records. It uses the
unified pipeline, risk engine, generic schema-2 snapshots, `RefreshService`, and
route reevaluation. There is no hurricane routing engine or snapshot type.
The existing parsed NWS CAP record class (historically named `FloodAlert`) is
reused internally. Its lifecycle logic is shared with the flood adapter.

## Official sources

- [NWS alerts API](https://www.weather.gov/documentation/services-web-api):
  `https://api.weather.gov/alerts/active?area=NC`, with the existing identifying
  User-Agent, request timeouts, bounded same-origin pagination and failure status.
  `TropicalNWSClient` configures the existing `NWSClient` event filter.
- [NHC current products](https://www.nhc.noaa.gov/productexamples/):
  `https://www.nhc.noaa.gov/CurrentStorms.json`. Its documented `activeStorms`
  array supplies storm IDs/names, classification, numeric center position,
  intensity in knots, last-update time and advisory/GIS product metadata.
  [Official sample](https://www.nhc.noaa.gov/productexamples/NHC_JSON_Sample.json)
  and [format reference](https://www.nhc.noaa.gov/productexamples/NHC_Tropical_Cyclone_Status_JSON_File_Reference.pdf).

NHC summaries are contextual only. The selected JSON product supplies storm
center points and links to GIS files, rather than embedded affected-road
polygons. This implementation preserves those links without downloading or
interpreting their KML/KMZ/shapefile content. Center points, forecast tracks and
cones are not turned into road-risk footprints. No presentation HTML is scraped.
Classification is preserved as supplied; a Saffir–Simpson category is not inferred.
Intensity is retained in knots along with the raw value; other official fields,
including any additional wind/timing information, remain in context metadata.

## Event filtering and normalization

Supported primary events:

- Hurricane Warning / Watch
- Tropical Storm Warning / Watch
- Storm Surge Warning / Watch

Defensive matching also accepts directly named Hurricane, Tropical Storm,
Storm Surge, Typhoon and Tropical Cyclone products ending in Warning, Watch,
Advisory, Statement or Local Statement. Matching is anchored and case-insensitive.
Generic high-wind, extreme-wind, hurricane-force marine wind, tornado, thunderstorm,
flood and heat alerts are not classified as hurricane hazards. Extreme Wind
Warnings can have non-tropical causes; no tropical association is inferred here.

Each normalized hazard has `hazard_type='hurricane'`, `id='nws:<official CAP ID>'`,
the unchanged official Polygon/MultiPolygon and issuance timestamp. Metadata
retains official event/severity, effective/onset/expiry/end/fetch times,
CAP certainty and parameters, instructions, descriptions, references and source
endpoint. Optional storm name/ID parameters are preserved without parsing names
out of prose. The `effect` field distinguishes tropical wind, storm surge, and
contextual tropical products; `alert_stage` distinguishes watch/warning/etc.

Only supplied valid polygons become spatial hazards. Missing geometry remains
context with an exclusion reason and partial assessment. The
[NWS geolocation guide](https://www.weather.gov/media/documentation/docs/NWS_Geolocation.pdf)
describes zone-based alerts; this adapter does not fetch zone boundaries or
substitute a state-wide polygon. An offshore storm alone never marks NC roads.

The shared parser retains one alert per official ID and reports duplicates.
Updates/cancellations withdraw referenced evidence; stale/future cancellation
references cannot withdraw current evidence. Unknown lifecycle, test/exercise,
expired and unmappable records remain contextual. Equivalent repeated IDs do not
add penalties twice. Distinct official wind, surge and flood events remain
separate contributors; cross-product correlation beyond IDs/references is not
inferred. Register each source once; generic cross-adapter ID conflicts retain
the existing pipeline's explicit rejection/warning behavior.

## Risk policy and storm surge

Default `HurricanePolicy` maps event names to existing risk levels:

| Event suffix | Level | Default edge penalty | Passable |
|---|---|---:|---|
| Statement / Local Statement | SAFE (informational) | 0 | Yes |
| Watch / Advisory | CAUTION | 250 | Yes |
| Warning | HIGH_RISK | 2500 | Yes |

Penalties are existing illustrative meter-equivalent routing costs, not measured
physical risk. Normalization maps these levels to existing severity labels
`none`, `moderate`, `high`, or `critical`; official severity stays in metadata.
Ordinary warning polygons never imply every road is physically blocked. Reasons
say “Road intersects active Hurricane Warning area,” qualified as exposure.

An explicit event override to `IMPASSABLE` requires a nonempty `blocking_reason`.
That is a caller's routing policy, not an inferred official road closure. The
selected mapping and blocking justification are retained in hazard evidence.
Wind speed never automatically changes passability. Storm Surge Warning/Watch
areas use the same geometric intersection and policy mapping; no road-level surge
depth is inferred.

```python
from survival_geo import RiskLevel
from survival_geo.hurricane import HurricanePolicy

policy = HurricanePolicy(
    event_levels={'Hurricane Watch': RiskLevel.CAUTION},
    penalties={RiskLevel.SAFE: 0, RiskLevel.CAUTION: 400,
               RiskLevel.HIGH_RISK: 3000, RiskLevel.IMPASSABLE: 0},
)
```

Pass the same configured policy to adaptation and routing when changing penalties.
`HurricanePolicy` delegates non-hurricane hazards to the standard severity policy,
so flood and other normalized hazards remain supported. Default adaptation also
works with the ordinary `RiskPolicy` because normalized severity labels agree.

Flood + Hurricane + Storm Surge Warnings on one edge contribute independently:
default penalty `2500 + 2500 + 2500 = 7500`. The existing engine sums penalties,
uses maximum category for passability, and retains every contribution and source.
This rule is deterministic and does not make overlapping events physically
independent or scientifically calibrated.

## Freshness, refresh and offline persistence

NWS uses the existing freshness function and alert policy: 15-minute fetch age,
2-minute future-clock tolerance, effective/expiration times, inclusive expiry.
NHC uses the same mechanism with an explicit configurable 6-hour maximum summary
age because this product supplies a last-update time rather than an expiration.
That age is an application setting, not an official expiration or danger threshold.
Missing times remain unknown; no cached read refreshes source timestamps.

Stale NWS geometry stays visibly stale and has no penalty/blocking authority in
the unified pipeline. Expired warnings are excluded from new adapter output;
previously persisted warnings become EXPIRED through the generic offline evaluator.
NHC summaries never contribute a routing penalty, regardless of their intensity.
Configured numeric source confidence is not a meteorological probability; official
CAP certainty remains separate.

```python
from survival_geo.offline import Coverage, SnapshotStore, RefreshService, route_offline
from survival_geo.flood import registered_flood_adapters
from survival_geo.hurricane import registered_hurricane_adapters

coverage = Coverage('raleigh', (-78.75, 35.68, -78.52, 35.88))
adapters = {
    **registered_flood_adapters(coverage),
    **registered_hurricane_adapters(area='NC'),
}
service = RefreshService(SnapshotStore('data/nc-multi-hazard.json'),
                         coverage, adapters=adapters)
state = service.refresh()  # Explicit online calls; independently recorded.

# Supplied coordinates and destination records; local graph and cache only.
result = route_offline(service, 'data/raleigh.graphml', origin, destination,
                       candidates=local_destinations)
# Reconnect uses the existing service.refresh_and_reevaluate(...).
```

Registry entries `hurricane_nws` and `hurricane_nhc` are independent. Set
`include_nhc=False` to omit the contextual summary feed. An NHC outage does not
discard NWS polygons. Hurricane failure does not discard successful flood data;
the existing merge retains appropriate cached hurricane evidence, original times,
and an explicit failed-attempt status. Complete empty results retire old source
records; partial data does not imply deletion. Total failure preserves cache bytes.

All fields serialize through the existing generic snapshot. Offline routing uses
local GraphML with downloads disabled and does not invoke adapters. Inspect
coverage/source/freshness output: no active or applicable alert is not a statement
that roads are safe. See [generic offline documentation](MULTI_HAZARD_OFFLINE.md).

## Deterministic demo and optional live query

From `backend/`:

```sh
.venv/bin/python -m survival_geo.hurricane.demo
.venv/bin/python -m pytest -q
# Optional, real read-only API requests; no synthetic fallback:
.venv/bin/python -m survival_geo.hurricane.live
```

The deterministic demo blocks HTTP and socket connections throughout. Synthetic
official-style Hurricane, Storm Surge and Flood Warning polygons intersect only
edge `2 → 3`. Baseline `1 → 2 → 3` is 900 m, remains passable, and has 7500 policy
penalty. Safer `1 → 4 → 3` is 1100 m with zero exposure. One affected edge is
avoided. Output includes event/source/freshness/reasons, encountered/avoided IDs,
and successful schema-2 cached routing on the same alternative. Files are temporary.

The optional live command reports returned NC tropical-alert count, count with
usable official geometry, source statuses and issues, plus NHC storm summaries.
Unavailable counts are `null`; partial counts are lower bounds. Live weather and
source availability vary. Tests use synthetic fixtures/mocks, never live weather.

Deferred: zone-geometry retrieval, NHC GIS archive parsing, forecast wind-radius or
surge-depth modeling, cross-source scientific correlation, calibrated danger
thresholds and official road-closure feeds. No new dependencies were added.
