# Unified offline persistence and reconnect routing

This extends the existing `offline` package and uses the existing `Hazard`,
`HazardBatch`, `Snapshot`, `DataState`, `SnapshotStore`, `RefreshService`, and
routing functions. No new hazard/risk/route models or live disaster sources.
The older schema-1 flood APIs and demos continue to work unchanged.

## Static graph and dynamic state

Road geometry stays in the existing local GraphML file. Schema-2 JSON contains
only dynamic hazard/source state. Choose a path per region, for example
`data/raleigh-multi-hazard.json`; the caller owns the location. GraphML and
snapshots are combined at runtime. No graph, GPS hardware, map tiles, or
online destination discovery is included in the snapshot.

## Schema 2

```json
{
  "schema_version": 2,
  "snapshot_id": "uuid",
  "created_at": "2026-10-03T14:00:00+00:00",
  "coverage": {
    "region_id": "raleigh",
    "bbox": [-78.75, 35.68, -78.52, 35.88],
    "crs": "EPSG:4326"
  },
  "sources": {
    "adapter-name": {
      "status": "available",
      "fetched_at": "2026-10-03T14:00:00+00:00",
      "attempted_at": "2026-10-03T14:00:00+00:00",
      "data_origin": "live",
      "coverage": {},
      "issues": [],
      "context": [],
      "hazard_origins": {},
      "freshness_policy": {},
      "removed_hazard_ids": []
    }
  },
  "hazards": [],
  "warnings": [],
  "hazard_coverage_state": "unavailable"
}
```

Each hazard entry is the existing `Hazard.to_dict()` payload plus its owning
`adapter`: ID, extensible hazard type, GeoJSON geometry, severity, source,
confidence, observation timestamp and metadata. Metadata preserves evidence,
reason, freshness, original fetch time, expiry/effective times and data quality.
Hazards are stored once, not repeated inside each source. Context preserves
unmappable alerts and measurements such as USGS values; those are not risk hazards.
`hazard_coverage_state` describes supplied inputs, never verified road safety.

Load validates the envelope, version, coverage and timestamps. Invalid individual
hazards are skipped with issues and partial coverage; valid records survive.
Invalid source entries become unavailable with warnings. Duplicate IDs are
excluded rather than arbitrarily attributed. Missing optional source/confidence
fields become `unknown`/0, visibly non-authoritative. Missing freshness means
unknown. Unsupported versions and bad envelopes raise stable `CacheError` codes;
service loading reports them and still permits baseline routing on a valid graph.
Schema 1 remains supported by its original service configuration. There is no
implicit migration; use a separate schema-2 file and a successful generic refresh.

`SnapshotStore` validates, writes a temporary file beside the target, flushes and
fsyncs, then atomically replaces it. A pre-replacement failure preserves the old
file. Fresh in-memory routing remains possible with `WRITE_FAILED` and
`snapshot_persisted=false`.

## Adapter registration and freshness

Use `RefreshService(store, coverage, adapters=registry)` for generic operation.
`adapters=None` retains the legacy flood API; `adapters={}` is explicitly generic.
A registered callable accepts the evaluation time and returns `HazardBatch`.
The existing unified comparison API still accepts zero-argument adapters.

Adapters provide original timezone-aware `fetched_at`, hazards' observation
`timestamp`, and an explicit `metadata.freshness`. They may provide per-hazard
`metadata.fetched_at`, `expires_at` (or existing flood `expires`/`ends`),
`effective_at` (or `effective`) and `freshness_policy`. Per-hazard fetch time is
captured before merging, so a new snapshot cannot refresh an old retained row.
Source/hazard policy dictionaries accept `max_fetch_age_seconds`,
`max_observation_age_seconds`, and `future_clock_tolerance_seconds`.
Defaults reuse the existing utility: 15-minute fetch age, no generic observation
age limit, and 2-minute clock tolerance. These are configurable freshness choices,
not scientific danger thresholds. NWS and USGS retain their existing policies.

State distinguishes `LIVE_CURRENT`, `LIVE_STALE`, `CACHED_CURRENT`,
`CACHED_STALE`, `EXPIRED`, and `UNAVAILABLE`, plus explicit UNKNOWN and
NOT_YET_ACTIVE variants. Modes are LIVE/CACHED/MIXED/UNAVAILABLE and describe
provenance, not detected connectivity. A 30-second-old offline cache is current;
loading it does not change its original times. Freshness is reevaluated on every
routing call. Stale, expired, unknown and future evidence stays visible but does
not penalize/block roads or exclude destinations in the unified path. Expiry is
inclusive. Expiration of an alert is not evidence that physical conditions cleared.

## Refresh and partial failures

Refresh calls adapters independently, records attempts, and isolates exceptions.
A complete available result replaces its source, including a successful empty
result. Partial results upsert by hazard ID and retain unreturned cached records;
absence from partial data is not deletion. Older observations cannot replace newer
ones. Explicit `removed_hazard_ids` withdraw evidence even from partial responses
(e.g. NWS cancellation); adapters must only supply evidence-backed withdrawals.

Failed adapters retain cached rows and original timestamps, with unavailable
status and failure issues. One successful source can be saved together with
cached evidence from another. Context merges by source/record identity (USGS
uses time-series identity). Coverage/query metadata must match before fallback.
Registry names identify stable adapter/query configurations: when changing scope,
use a new key or store. A thrown exception cannot supply a new scope to validate.
Unregistered cached sources are retained as partial evidence with a warning.

If all adapters fail, or only return empty partial input, cache bytes remain
unchanged. Without a valid cache, no empty failure file is created. Attempt
failures remain visible in memory. Separate NWS and USGS registration allows NWS
success plus cached USGS context without discarding either source.

## Usage and disk-only execution

```python
from survival_geo.offline import (
    Coverage, SnapshotStore, RefreshService, route_with_state, route_offline,
)
from survival_geo.flood import registered_flood_adapters

coverage = Coverage('raleigh', (-78.75, 35.68, -78.52, 35.88))
registry = registered_flood_adapters(coverage)  # Existing clients; no fetch here.
service = RefreshService(SnapshotStore('data/raleigh-multi-hazard.json'),
                         coverage, adapters=registry)
# Explicit online refresh. The caller controls when this happens.
current_state = service.refresh()

# origin/destination are supplied (latitude, longitude) tuples.
# No refresh or adapter invocation; downloads disabled even on a missing graph.
result = route_offline(service, 'data/raleigh.graphml', origin, destination,
                       candidates=local_destination_records)
previous_state = service.load_offline()

# Explicit reconnect attempt; graph is already loaded locally.
new_state, change = service.refresh_and_reevaluate(
    graph, result['safer']['route'], previous_state, current_position=origin)
```

`route_with_state` and `route_offline` use the existing unified comparison and
freshness-aware destination filtering. Inspect `exists`, `hazard_data`, and
`data_state`; an available route or accepted destination is not proof of safety.
Missing hazard data permits baseline routing with explicit unavailable coverage.
Missing local GraphML raises `DataAccessError`, with no download fallback.

## Reevaluation and alternative routes

Reevaluation compares exact `(u, v, key)` edge risks under the previous/current
states' evaluation timestamps. Results include viability, changed edge risks,
newly penalized/blocked edges, new hazards and full evidence, escalation triggers,
resolved hazards, unknown applicability, snapshot/evaluation timestamps and
reason codes. A new/increased penalized or blocked condition recommends rerouting;
a still-blocked route or missing graph edge also recommends it. Informational
hazards do not trigger rerouting. Decreased risk alone does not recommend a reroute.

Confirmed expiry, explicit source withdrawal, or a complete current refresh can
establish that evidence no longer affects the route. Stale or failed sources
cannot establish resolution. Unrelated source failures do not invalidate a
successful source's removal evidence.

Automatic recalculation calls the existing unified risk-aware routing. It returns
UPDATED, SAME_ROUTE or NO_ROUTE and preserves old/current route summaries,
alternative distance, risk penalty/exposure differences, affected/avoided counts
and triggering hazards. There is no fallback to a blocked baseline when no
passable alternative exists. Set `recalculate=False` to inspect changes only.
Trim traveled edges before reevaluation when only the remaining trip matters.
If `current_position` differs, distance differences compare the new trip from
that position with the supplied old route; these are not like-for-like trip lengths.

## Deterministic demo and tests

```sh
.venv/bin/python -m survival_geo.offline.multi_hazard_demo
.venv/bin/python -m pytest -q
```

The demo blocks HTTP and socket connections throughout. Initial route and
GraphML/cache-only route are `1 → 2 → 3` (900 m). Total source failure leaves
cache bytes unchanged. At reconnect a synthetic closure intersects only edge
`2 → 3`; reevaluation recommends rerouting to `1 → 4 → 3` (1100 m), avoiding one
blocked edge. Output contains evidence, timestamps, CACHED_CURRENT/LIVE_CURRENT
states, and zero network/ offline-adapter-call counts. Files live in a temporary
directory that is removed after the demo. The previous flood/offline/live demos
remain separate and available.

Limits: one writer per snapshot file; no backups/history, background refresh,
connectivity detector, arbitrary schema migration, complete source-coverage
guarantee, hydrologic/scientific thresholds, or new disaster adapters. Freshness
requires trustworthy adapter timestamps and metadata. Cached data cannot reveal
unreceived cancellations. The existing road snapping, whole-edge risk, and
physical-safety limitations remain.
