# Validation record

Verified on **October 3, 2026**, using Node.js **24.19.0** on Windows.

## Automated tests

`node --test tests/*.test.mjs` — **67 passed, 0 failed**.

Coverage includes the original geometry, provider, persistence and HTTP checks, plus:

- Routes beginning inside each of the three fictional disasters in all three NC presets.
- Ranked reachable destinations outside the selected warning, with no re-entry after exit.
- Strict separation of independent demo disasters and all-hazard checks for live routing.
- Fictional dry corridors rejected unless both graph and incident are simulated.
- Road reports overriding simulated dry corridors; no fabricated live flood exit.
- No-warning, no-destination, no-graph, missing-boundary and invalid-coordinate outcomes.
- Missing, stale and invalid source timestamps; live offline pack age limits.
- Origins outside a warning do not imply a need to evacuate.
- Generic picnic/bus shelters and explicitly restricted resources excluded from suggestions.
- Road snapping cannot bypass an excluded flood area.
- Personal evacuation HTTP validation and selected-disaster briefing isolation.
- OSM-backed scenarios preserve original source timestamps and leave the live graph unchanged.
- Shortest comparisons use matching road access points, retain one-way restrictions, and identify excluded geometry.
- Identical shortest and selected routes do not claim a detour benefit; offline comparison results match online logic.
- Failed OSM requests remain visibly fictional when a demo fallback is needed; route responses include the exact map snapshot used.

## Current real-street upgrade

All three example disasters produced connected routes and comparisons using actual OSM graphs in Raleigh (128,310 segments), Wilmington (68,707), and Asheville (106,984). Asheville initially returned an explicit fallback during a provider outage, then recovered; the final verification used `roadInfo.basis: osm` for every Asheville result. Map metadata now travels with route results to preserve matching provenance after recovery.

The previously reported custom flood point (35.79015, -78.62016) now returns **Finch Library, 2.1 km by road**, with **0.2 km caution exposure**, versus **0.4 km** on the shortest comparison. A 33 m access gap remains explicitly unverified. This was verified through both HTTP and browser controls. Three other nearby custom flood origins also returned OSM routes.

Saved the complete real-road Raleigh pack, stopped the server, reloaded through the service worker, and recalculated the same custom route and comparison entirely in the browser. The footer displayed **Offline pack**. Restarted the server afterward.

Desktop and 390 px mobile layouts were inspected; the mobile document was 375 px wide with no horizontal overflow. Both comparison cards, the overlay toggle, and destination alternatives remained usable. Demo road-condition assumptions and resource availability remain unverified.

The grid-based results recorded below are historical checks of the earlier synthetic demo, which remains available with `DEMO_ROADS=synthetic`.

Tests use an isolated local child server and temporary SQLite database with paid integrations disabled.

## Personal-planner browser checks

- Flood: Use example location → Find my exit route produced **Oakwood community hub, 3.5 km**, starting inside the fictional warning. Two other destinations were available.
- Hurricane: the example origin produced **Northside library, 5.4 km** after the road-grid update, with **Oakwood, 5.9 km** as an alternative (verified through the shared planner).
- Heat: the example origin produced **Greenway health clinic, 3.5 km**.
- Switching disasters removed the old route and example origin. The map stays focused on one warning category. The updated flood demo also displays its fictional dry roads and two red flooded patches.
- Selected heat guidance contained only heat guidance and was labeled **Local template** without credentials.
- Live Raleigh: a manually chosen downtown point returned **No matching warning in this view**, with no fabricated route or safety claim. Live destination options excluded picnic shelters and gazebos.
- Saved a new offline pack, stopped the app server, reloaded from the service worker, and recalculated the **same 3.5 km flood exit** entirely in the browser. The map footer identified the saved offline pack. The server was restarted afterward.
- Desktop and narrow preview layouts were inspected. The narrow document measured **366 px** within a **378 px** viewport, with no horizontal overflow. The route result remained accessible beneath the map.
- Normal online interactions produced no observed JavaScript errors. Expected network errors during the deliberate server-stop test led to the offline fallback.

## Public connector smoke check from initial implementation

The earlier `node scripts/check-live.mjs` run passed for all three presets:

| Region | OSM resource locations | Road segments | USGS readings |
| --- | ---: | ---: | ---: |
| Raleigh | 130 | 128,310 | 102 |
| Wilmington | 23 | 68,707 | 103 |
| Asheville | 55 | 106,984 | 102 |

These are raw provider counts at test time, not eligible destination counts or guarantees of current availability. NOAA alerts/weather, USGS and OSM had successful current/cached responses. Census was marked needs-key. A transient Wilmington Overpass failure was accurately reported before a later successful retry. The personal-planner browser checks above separately verified current Raleigh data.

## Not externally verified

- Gemini cloud generation, ElevenLabs cloud speech, Tiger Cloud writes/aggregates, and a Databricks-backed ingestion bridge require the user's credentials/services. The PostgreSQL driver is installed.
- Census with an authenticated key; public deployment; Docker execution; GPS permission flow; PWA installation on other devices.
- Real disaster navigation, road passability, turn restrictions, bridge conditions, official shelter status, emergency-service integration, and routes beyond the fixed city coverage windows.

This is a working local planning prototype, not a validated operational evacuation system.

## Custom flood-origin regression

The selected point (35.79015, -78.62016) originally failed because it was over 500 m from a grid intersection. A point only a few meters from the example origin also failed its access check. The expanded demo network and road-segment projection now handle both cases. The original point returns a 3.1 km route to Greenway health clinic, with a separately labeled 4 m unverified access gap. A direct browser map click at another point (35.78781, -78.61662) also returned a 3.1 km route, with a 52 m gap.

Regression tests sample custom points across all three regional flood polygons, verify access and road geometry never cross red flooded patches, retain no-route behavior inside those patches, preserve one-way direction at a road midpoint, reject old offline packs, and prevent demo exceptions in live mode even if simulation flags are present.


The updated offline pack was also tested with the local server stopped: the same custom point and Greenway destination returned the 3.1 km route entirely in the browser, labeled Offline pack. The server was then restarted.
