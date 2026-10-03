# Wayfinder NC: first PWA

React 19 + TypeScript + Vite 7, Leaflet 1.9, runtime response validation with Zod,
and a thin FastAPI API. Python remains the source of truth: no routing, severity
mapping, relief ranking, or spatial hazard decisions are duplicated in JavaScript.
No frontend framework or HTTP server existed before this change.

## Run locally

Python 3.9+ (the existing backend environment) and Node 22.12+ are required.
From the repository root, start the backend:

```sh
cd backend
.venv/bin/python -m pip install -r requirements-api.txt
.venv/bin/python -m uvicorn survival_geo.api.app:app --host 127.0.0.1 --port 8000
```

In another terminal:

```sh
cd frontend
npm ci
npm run dev
```

Open the Vite URL, normally `http://127.0.0.1:5173`. Vite proxies `/api` to the
loopback backend. No wildcard CORS configuration is required. This is a local,
single-process hackathon application; do not expose the unauthenticated API as a
public multi-user service. No accounts/authentication were added.

To test installation and the production service worker:

```sh
cd frontend
npm run build
npm run preview -- --port 4173
```

Open `http://127.0.0.1:4173` with the backend still running. Development mode does
not register the service worker. The build generates `dist/sw.js` with all hashed
JS/CSS, the index, manifest and PNG/SVG icons. Serve `dist` and proxy `/api` on the
same origin for deployment. Vite preview is a local verification server, not a
production hosting configuration. No deployment was performed.

## Ten-step deterministic demo

Default backend startup is **DEMO MODE**, using existing heat demo fixtures, the
existing NWS flood/heat/hurricane parsers and adapters, and a small local GraphML.
Every event and destination is synthetic and visibly labeled. The fixed scenario
clock is 2026-10-03 14:10 UTC (displayed in browser local time). Synthetic results
have `local`/`cached` provenance, never live. Nothing depends on active disasters,
external map tiles, APIs, geolocation permissions, or connectivity to the internet.

1. Open the app. The configured demo point at node 1 appears on the map. It is
   explicitly a demo point, not GPS. `Use my location` requests browser geolocation;
   denial/unavailability gives a coordinate fallback. Actual positions outside the
   downloaded graph are rejected rather than snapped across an arbitrary distance.
2. Cooling Center A is initially selected. Click **Compare routes**.
3. Flood, hurricane, storm-surge and heat geometries appear along the normal route.
4. Normal route: `[1,2,3]`, **900 m**, one intersecting edge, **10,000** total policy
   penalty (four distinct 2,500 contributions). Safer route: `[1,4,3]`, **1,100 m**,
   no supplied hazard intersections/penalty. Difference: **+200 m**, one edge avoided.
5. Open **Why this route?** for backend events, source, freshness, affected segments,
   reason text and avoidance. These are deterministic contributions, not AI prose.
6. Cooling Center A is a synthetic trusted official-style fixture. Library B is a
   synthetic OSM-style **potential relief location**, route `[1,4,5]`, **1,150 m**.
   Opening hours, actual cooling capability and capacity remain unverified.
7. Click **Simulate offline**. Python reloads local GraphML and schema-2 snapshot;
   source adapters are not called. The existing route remains visible.
8. The status shows **OFFLINE**, cached source evidence, and an explicit warning
   that the displayed route has not been revalidated. New calculations still work
   if this local Python server is reachable.
9. Click **Reconnect + new hazard**. The fixture refresh adds a hurricane warning
   on `(4,3,0)`. The selected route is passed to the existing `reevaluate_route`.
10. The updated safer route is `[1,6,3]`, **1,300 m**, avoiding the new affected edge.
    **Reset demo** restores the initial source state and clears route sessions.

Desktop has a map and scrollable side panel; phones have the map above the route
controls. Marker colors, dashed normal routes, solid alternate routes, labels, a
legend and textual equivalents avoid relying solely on color. Install/help,
geolocation, coordinates, destination radios and details are keyboard accessible.

## API and backend reuse

All responses have `Cache-Control: no-store`; the service worker never intercepts
API requests. FastAPI OpenAPI documentation is available at `http://127.0.0.1:8000/docs`.

| Endpoint | Behavior |
|---|---|
| `GET /api/health` | Process health and API version |
| `GET /api/status` | Demo/mode, origin, local road GeoJSON, evaluated DataState, hazards, context and category summaries |
| `GET /api/hazards` | Normalized hazards, context, category summaries, DataState |
| `POST /api/destinations` | `{ "origin": { "latitude": 35.77, "longitude": -78.64 } }`; existing relief evaluator |
| `POST /api/route` | Origin plus `destination_id`; existing eligible candidate route comparison and server-issued `route_id` |
| `POST /api/refresh` | Existing generic RefreshService; preserves cached/partial failure semantics |
| `POST /api/offline` | Load local GraphML and generic snapshot with no downloads/adapter calls |
| `POST /api/route/reevaluate` | `{ "route_id": "..." }`; existing reevaluation plus destination recheck |
| `POST /api/demo` | `{ "action": "offline" \| "reconnect" \| "reset" }`; disabled in real mode |

Coordinates and requests are validated. Invalid inputs return a bounded structured
error, not a Python traceback. Unknown routes, no eligible destination, missing
coverage and unexpected failures have user-facing error states. Route sessions retain
the exact prior backend state, are capped at 64, and expire on process restart/reset.
The browser retains geometry but must calculate again if its route session expires.
Calls are serialized with a runtime lock to respect the existing single-writer store.

`route_with_state`, `evaluate_relief_candidates`, `reevaluate_route`, `RefreshService`,
`SnapshotStore`, `load_road_graph(allow_download=False)`, and existing parsers/policies
are reused. Hazard category summaries describe returned source evidence, including
geometryless active alerts. They do not decide routing. Empty successful source results
can say “no applicable active alert”; failed/stale sources cannot imply clearance.

## Real data

Set `WOLFHACKS_CONFIG` to a local JSON configuration based on
`backend/survival_geo/api/real.example.json`. Paths resolve from the backend working
directory. Prepare a valid EPSG:4326 local GraphML, matching coverage, and a JSON
array of candidates using the existing discovery/ingestion functions described in
`backend/HEAT.md`. At minimum each candidate needs stable string `id`, latitude and
longitude; names/categories are optional. Official designation requires the existing
trusted-source evidence contract. Default `trusted_sources` is empty.

```sh
cd backend
WOLFHACKS_CONFIG=/absolute/path/to/region.json \
WOLFHACKS_DATA_DIR=data/pwa-real \
.venv/bin/python -m uvicorn survival_geo.api.app:app --host 127.0.0.1 --port 8000
```

Real mode initially loads the existing local snapshot without network calls. Click
**Refresh & check route** to query the registered existing NWS/USGS/NHC/heat adapters
and optional NWS weather station. No successful refresh is fabricated. A missing
snapshot gives unavailable coverage. Real-mode candidates are supplied local records;
this API does not automatically run an OSM search or download a new road region.
The UI uses the same validated responses/components in demo and real modes.

Demo snapshots default to `backend/data/pwa-demo`; real snapshots default to
`backend/data/pwa-real`. Keep those directories separate. Current mode and workspace
identity come from the backend; incompatible saved workspace routes are cleared when
the server is reached. A downloaded prior workspace can remain visible during an
outage and is explicitly labeled as a downloaded view.

## PWA and exact offline behavior

[Install requirements](https://developer.mozilla.org/en-US/docs/Web/Progressive_web_apps/Guides/Making_PWAs_installable):
use HTTPS or localhost and a supported browser. The manifest provides standalone
mode, scoped start URL, theme metadata and 192/512 PNG icons. When supported, **Install
app** invokes the browser prompt. Otherwise **Install** explains browser-menu or
iPhone Share → Add to Home Screen installation. Installation UI differs by browser;
OS-level installation was not automated. Phones accessing a laptop over plain LAN
HTTP do not get the same secure-context PWA/geolocation guarantees; use trusted HTTPS.

The production worker precaches the complete built shell. Browser localStorage saves
one validated last-view document: roads, hazard geometry/context, destinations, selected
route comparisons, origin and original evaluation timestamps. Storage errors are shown.
A service-worker registration failure is also shown. Browser eviction/private mode can
remove cached data; no offline availability is promised before the first successful load.
Updates activate after existing tabs close; close/reopen to use a newly built version.

**Frontend without any backend connection:** installed shell launches; downloaded
roads, hazard overlays, location and prior route remain visible. No new route,
reclassification, authoritative freshness evaluation, refresh or reevaluation runs in
JavaScript. Old real evidence is flagged for backend revalidation after one minute;
this is a display reminder, not a duplicate hazard-policy implementation. Downloaded
freshness labels are explicitly historical. The browser never calls cached data live.
An initial offline visit with no saved state shows a useful unavailable message.

**With a reachable local Python runtime but no internet:** use **Use downloaded data**
in real mode (or demo offline). GraphML + schema-2 snapshot support new routes through
the original engine; freshness is reevaluated by Python, preserving stale/expired/unknown
semantics. Browser network-loss handling also attempts this endpoint when localhost
remains reachable. Failure leaves the prior browser view intact. A remote Python server
is not made available by installing a service worker.

**Reconnect:** an online event attempts refresh (demo injects its new hazard), then
reevaluates the stored route. The button provides a repeatable manual alternative.
Partial-source failures remain visible and retain cached evidence. A newly invalidated
destination is explicitly marked ineligible, not silently recommended. Browser online
status is only a connectivity hint; API errors still show when the server is unreachable.

**Map tiles:** no external raster/vector tiles are requested, cached or predownloaded.
Leaflet displays downloaded road GeoJSON, hazard areas, markers and route geometry on
a neutral grid. No arbitrary street basemap, street labels, geographic search or enormous
regional download is claimed. This makes the deterministic demo independent of tile
providers and keys. The app is for route comparison/monitoring, not turn-by-turn navigation.

## Verification

```sh
cd backend
.venv/bin/python -m pytest -q
cd ../frontend
npm test
npm run build
npx playwright install chromium
npm run test:e2e
```

The browser test command starts/stops its own backend and preview server on ports
8000 and 4173; stop manually launched servers on those ports first. For this session,
Chromium was installed in `/private/tmp/wolfhacks-playwright`, so the command was:
`PLAYWRIGHT_BROWSERS_PATH=/private/tmp/wolfhacks-playwright npm run test:e2e`.

Verified: **409 backend tests passed** (390 existing + 19 API tests), **25 frontend tests
passed**, production TypeScript/Vite build passed, **6 Chromium end-to-end scenarios
passed** across desktop and phone-sized layouts. These include actual network-disabled
reload, a controlled service worker, no cached API responses, retained route geometry,
reconnect rerouting, geolocation denial and no horizontal overflow. Both screenshots
were visually reviewed. API tests forbid external networking for the synthetic demo
and test real-mode local configuration, outages, no snapshot, no route, unknown requests,
stale/expired/geometryless alerts and a newly invalidated destination.

Test tooling explicitly disables Node's native experimental Web Storage inside Vitest
workers so jsdom provides browser storage. npm dependency audit reports zero
vulnerabilities after updating Vitest. Existing Python urllib3/LibreSSL warning remains;
Playwright emits a harmless NO_COLOR/FORCE_COLOR environment warning. No live external
source verification or real-location browser routing was performed in this task.

Deferred: production hosting/multi-user isolation, authenticated deployment, regional
map/candidate download management, scalable destination evaluation, automatic GPS
tracking/turn-by-turn instructions, verified operational cooling-center feeds, full
screen-reader/map accessibility audit, Safari/Firefox/device-specific installation
verification. No sponsor integrations, accounts, reporting, databases or LLMs were added.

## Exact change manifest

Changed:
- `.gitignore`

Created:
- `backend/requirements-api.txt`
- `backend/survival_geo/api/__init__.py`
- `backend/survival_geo/api/app.py`
- `backend/survival_geo/api/runtime.py`
- `backend/survival_geo/api/real.example.json`
- `backend/tests/test_api.py`
- `frontend/PWA.md`
- `frontend/package.json`
- `frontend/package-lock.json`
- `frontend/index.html`
- `frontend/tsconfig.json`
- `frontend/vite.config.ts`
- `frontend/playwright.config.ts`
- `frontend/public/manifest.webmanifest`
- `frontend/public/icon.svg`
- `frontend/public/icon-192.png`
- `frontend/public/icon-512.png`
- `frontend/scripts/build-sw.mjs`
- `frontend/scripts/generate-icons.py`
- `frontend/src/main.tsx`
- `frontend/src/App.tsx`
- `frontend/src/MapView.tsx`
- `frontend/src/api.ts`
- `frontend/src/components.tsx`
- `frontend/src/storage.ts`
- `frontend/src/styles.css`
- `frontend/src/test-setup.ts`
- `frontend/src/app.test.tsx`
- `frontend/src/__fixtures__/demo.json`
- `frontend/e2e/app.spec.ts`

Generated `frontend/dist` (including the service worker), test screenshots/traces,
node_modules and backend runtime data are ignored, not source changes. Existing
hazard/routing/offline implementations and all existing test files were left unchanged.
