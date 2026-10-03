# Offline snapshots and reconnect routing

For schema-2 multi-hazard persistence using the same offline package, see
[Unified offline persistence](MULTI_HAZARD_OFFLINE.md). The schema-1 flood API
described below remains supported.

The offline layer composes the existing flood adapters, freshness utilities,
standardized hazards, destination filtering, and routing engine. The caller
explicitly chooses an offline load or a refresh attempt. There is no connectivity
probe, background polling, phone GPS access, or frontend-specific behavior.

```text
ONLINE
NWS + USGS → Normalize → Local snapshot → Routing

OFFLINE
Local GraphML + snapshot + supplied GPS coordinates → Routing

RECONNECT
Fresh APIs → Compare state → Reevaluate route → Reroute if needed
```

## Modules

- `offline/snapshot.py`: schema version 1, coverage metadata, validated JSON codec.
- `offline/store.py`: local file loading and atomic replacement.
- `offline/state.py`: evaluated live/cached/expired state and provenance.
- `offline/service.py`: explicit refresh, per-source fallback, partial-response merge.
- `offline/routing.py`: local routing, destination filtering, route reevaluation.
- `offline/demo.py`: deterministic disk-backed online/outage/reconnect simulation.
- `roads.py`: adds `allow_download=False` to the existing graph loader.

## What is stored

Choose one snapshot file per region, for example `backend/data/raleigh-snapshot.json`.
The caller chooses the path; there is no hidden global cache directory. Existing
GraphML persists the road network separately (`backend/data/raleigh.graphml`).

```json
{
  "schema_version": 1,
  "snapshot_id": "generated-uuid",
  "created_at": "2026-10-03T14:00:00+00:00",
  "coverage": {
    "region_id": "raleigh",
    "bbox": [-78.75, 35.68, -78.52, 35.88],
    "crs": "EPSG:4326"
  },
  "sources": {"nws": "SourceResult object", "usgs": "SourceResult object"},
  "hazards": ["standardized Hazard objects"]
}
```

The example abbreviates nested objects. Each source stores its endpoint, status,
query scope, data origin, fetch/attempt timestamps, issues, and normalized records.
NWS records retain official IDs, footprints, sent/effective/onset/expiration/end
times, descriptions and instructions. USGS records retain site/time-series IDs,
point geometry, measurement time/value/units and quality metadata. Geometries use
GeoJSON; timestamps use timezone-aware ISO strings. No API keys are stored.

The serialized hazard list is a historical derivative at snapshot creation. It
is validated against the cached source records on load. Routing **rebuilds**
hazards from those records at the requested evaluation time; it never blindly
uses a historical hazard list after expiration. This schema currently represents
the flood pipeline, not arbitrary new hazard-source adapters.

Not stored here: the road graph, map tiles, GPS hardware data, destination rankings,
destination-discovery results, routes, or user reports. Offline callers supply a
destination and optionally locally available candidate destination dictionaries.
No OSM discovery call occurs during offline filtering.

## Write safety and validation

`SnapshotStore.save` validates the complete snapshot, writes a temporary file in
the destination directory, flushes and fsyncs that file, then calls `os.replace`.
Readers see a complete old or new snapshot. Failure before replacement preserves
the original; a process crash may leave an unused temporary file, which loaders
ignore. The graph is not rewritten during dynamic refresh.

`SnapshotStore.load` raises a controlled `CacheError` with codes including
`MISSING`, `MALFORMED`, `INCOMPATIBLE_VERSION`, `COVERAGE_MISMATCH`, or `READ_FAILED`.
The orchestration layer exposes these as `cache_status` / `cache_error` and an
UNAVAILABLE state instead of assuming no hazards. Geometry, required timestamps,
provenance, source statuses, and hazard/source consistency are checked. Version 1
has no migration path; a successful refresh can build a new valid snapshot.

Writes assume one application writer per file. Atomic visibility is not a
multi-writer transaction, history/backup service, or complete power-loss durability
guarantee on every filesystem. If a write fails, fresh in-memory data remains
usable, `cache_status=WRITE_FAILED`, and `snapshot_persisted=false`; the older
on-disk snapshot remains available. No automatic cache eviction is performed.

## Data modes and freshness

`DataState.mode` describes provenance, not network detection:

| Mode | Meaning |
| --- | --- |
| LIVE | Both represented sources came from the current refresh |
| CACHED | Represented sources came from disk/fallback |
| MIXED | Live and cached sources/records coexist, or only some sources are available |
| UNAVAILABLE | No usable source snapshot exists |

Each source and record separately reports `data_state`, including `LIVE_CURRENT`,
`LIVE_STALE`, `CACHED_CURRENT`, `CACHED_STALE`, `EXPIRED`, and `UNAVAILABLE`.
`LIVE_UNKNOWN` / `CACHED_UNKNOWN` and `*_NOT_YET_ACTIVE` preserve missing or future
timing information. A source with different record states reports `MIXED` and
retains each record's label. Current fetch age does not make an old gauge reading
current. Mode LIVE does not override a stale record.

State output includes schema version, snapshot ID/creation time/age, coverage,
source and record timestamps/ages, source query scopes, source status, refresh
attempt results, and `unavailable_during_attempt`. An explicit offline load has
`attempts={}`: it does not invent a network failure. Source statuses retained
from disk describe the stored attempt; they do not imply a new request happened.
Cached records always have cached provenance after loading, even if the original
snapshot was fetched live. Rewriting a merged file does not reset old fetch times.

The flood defaults are reused: maximum fetch age 15 minutes, gauge measurement
age 2 hours, and 2-minute future-clock tolerance. Alerts use their validity window
instead of a fixed issuance-age limit. See [FLOOD.md](FLOOD.md) for configuration.
`include_stale=False` excludes stale alerts from routing but retains their records
and stale labels. Explicit `include_stale=True` retains their penalties, still
marked stale. Expired alerts never contribute routing hazards, even with opt-in.
Expired records remain inspectable with their original expiration times.

An unavailable/expired/stale feed or absence of an alert is not evidence of
physical safety. Generic SAFE and passability fields retain their policy-only
meaning. Distance routing remains possible with missing hazard data and explicit
UNAVAILABLE/degraded metadata.

## Refresh and partial failure rules

`RefreshService.refresh` calls the configured NWS/USGS adapters. It does not probe
connectivity, download roads, discover destinations, or run a timer. Adapter
timeouts and pagination bounds remain configurable through the supplied clients.

1. A complete AVAILABLE response replaces that source, including a successful
   empty response. This permits expired/cancelled/no-longer-returned alerts to
   disappear after a successful complete refresh.
2. An UNAVAILABLE source uses its cached counterpart only if source endpoint and
   query scope match. Its failed attempt stays visible and old times are retained.
3. A PARTIAL source upserts returned records while retaining cached records absent
   from the incomplete response. Absence on a missing page is not deletion.
   NWS identity is alert ID. USGS identity uses time-series ID when present, with
   observation ID as fallback. A partial older gauge observation cannot replace
   a newer one in the same series. Per-record live/cached origins remain visible.
4. If one source succeeds, its fresh data and any matching fallback records are
   persisted together. A new snapshot creation time does not rejuvenate old rows.
5. If both sources fail, or both yield empty incomplete responses, the previous
   file is left byte-for-byte unchanged. Without a prior file, no empty failure
   snapshot is written. A later complete refresh replaces accumulated partial data.

Partial snapshots may temporarily retain superseded records. The existing flood
adapter applies cancellation/update references present in the available data and
expiration checks. Missing cancellation messages cannot be inferred offline.

## Example workflow

Run from `backend/` with the environment active. The initial online setup is
explicit; paths under `data/` are ignored by Git:

```python
from survival_geo import load_road_graph
from survival_geo.offline import (
    Coverage, SnapshotStore, RefreshService, route_with_state,
    route_offline, reevaluate_route,
)

origin = (35.7796, -78.6382)  # supplied by caller/device
destination = (35.7850, -78.6350)
coverage = Coverage('raleigh', (-78.75, 35.68, -78.52, 35.88))
service = RefreshService(SnapshotStore('data/raleigh-snapshot.json'), coverage)
graph = load_road_graph(*origin, radius_m=5000, graph_path='data/raleigh.graphml')
online_state = service.refresh()
online = route_with_state(graph, origin, destination, online_state)

# Internet unavailable: these calls perform zero network operations.
offline_state = service.load_offline()
offline = route_offline(service, 'data/raleigh.graphml', origin, destination,
                        candidates=[])  # optionally supply local discovered candidates

# User/system explicitly requests refresh after reconnect.
current_state, change = service.refresh_and_reevaluate(
    graph, offline['flood_aware']['route'], offline_state,
    current_position=origin,
)
print(change['reroute_recommended'], change['alternative_route'])
```

Check `exists` before supplying a route to reevaluation. If the GraphML is missing,
offline routing raises `DataAccessError`; it never falls back to downloading.
Missing/corrupt hazard snapshots allow routing with explicit unknown/degraded
hazard status. Geometry and candidate filtering run entirely locally.

Coverage is a caller-declared EPSG:4326 bounding box. The graph's edge extent must
fit inside it. NWS query area defaults to NC and should cover the full routing
region; USGS context is queried around the region center. Coverage metadata does
not guarantee complete official hazard coverage. Use a separate file for another
region or query scope; mismatched cached queries cannot act as fallback.

## Route reevaluation and recalculation

`reevaluate_route` assesses the exact `(u, v, key)` edges, preserving parallel-edge
identity. It evaluates previous/current hazards with the existing risk engine.
Default flood policy flags new or increased HIGH_RISK conditions for rerouting;
any IMPASSABLE condition from a caller-supplied policy or missing graph edge also
triggers recalculation. No scientific flood thresholds or LLM decisions are used.
New CAUTION intersections are reported without an automatic reroute recommendation.

Returned fields include:

- `route_still_viable`: whether all supplied edges exist and pass the current policy.
- `reroute_recommended`, `newly_affected_edges`, `newly_encountered_hazard_ids`.
- `hazards_no_longer_applicable` with reason codes; `hazard_applicability_unknown`
  when older evidence merely became stale, unmappable, or unavailable.
- Previous/current evaluation and snapshot timestamps.
- `old_route_summary`, `current_route_summary`, and `alternative_route`.
- `route_updated`, `alternative_status`, `reason_codes`, and current data metadata.

Expiration is reported as `ALERT_EXPIRED`, not proof that flooding ended. A fresh,
complete source refresh can establish that an old alert no longer applies to the
route; missing geometry or stale data cannot. Warning areas remain passable by
default, so `route_still_viable=true` can coexist with `reroute_recommended=true`.

Recalculation calls the existing `build_risk_route`. It may return UPDATED,
SAME_ROUTE (no different lower-cost path), or NO_ROUTE (no passable path). Both old
and new summaries remain available even when there is no alternative. Set
`recalculate=False` to inspect the decision without recalculating.

Reevaluation examines the entire supplied route. The caller must trim traveled
edges if only the remaining trip should be considered. `current_position` changes
the starting point of the recalculated route, not the inspected edge list. A
materially different graph should trigger a new route; historical graph versions
are not stored. Physical safety and official road-access status remain unknown.

## Deterministic demo and tests

```sh
python -m survival_geo.offline.demo
python -m pytest -q
```

The demo creates temporary GraphML and snapshot files, uses synthetic official-
schema responses, and blocks both HTTP requests and socket connections throughout:

| Step | Route | Data/result |
| --- | --- | --- |
| Initial mocked refresh | `1 → 2 → 3`, 900 m | LIVE; snapshot saved |
| Simulated source failures | — | CACHED; original cache bytes preserved |
| Disk-only offline routing | `1 → 2 → 3`, 900 m | CACHED_CURRENT; no source fetch |
| Mocked reconnect | `1 → 4 → 3`, 1100 m | New warning intersects edge `2 → 3`; snapshot replaced |

The warning adds the existing policy's penalty, not an inferred closure. The demo
prints full JSON with timestamps, freshness, and old/new route summaries, then
removes its temporary directory. The original synthetic and optional live flood
demos remain available. Tests prohibit network access and cover disk corruption,
atomic-write failures, source outages, mixed freshness, expiration, filtering,
route changes, and no-alternative cases.
