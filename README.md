# WayAhead

A working North Carolina disaster-planning application for **hurricanes, floods, and heat waves**, built for the Center for Geospatial Analytics track.

Choose **one disaster**, set your starting location, and get **suggested nearby resources with road routes beyond the warning area**. The main screen is a personal evacuation planner. Provider details and supporting sensor readings live in Data & help. The demo uses **real OpenStreetMap streets and resource locations with simulated disaster conditions**; live mode uses actual public warning feeds.

Try it: **Flood → Use example location → Find my exit route**. The example starts inside a simulated warning and compares real road routes to mapped resources. **Why this route?** compares the chosen road route with the shortest road route to the same destination, showing distance, warning exposure, and excluded segments. Toggle the shortest route on the map for comparison. Switch to Hurricane or Extreme heat to try an independent scenario. Actual GPS is used only after choosing Use my location; the app never substitutes a city center for your location.

## Run locally

**Requirement: Node.js 24 or newer.** No Python, frontend build, database service, or API key is required for the core app.

```sh
node --env-file-if-exists=.env server.mjs
```

Open **http://127.0.0.1:4173**. On Windows you can also run `./start.ps1`. With npm installed, `npm start` is equivalent.

The first load downloads OSM roads and resources and may take 15–65 seconds. The server caches provider responses in `data/terrawatch.sqlite`. Coverage presets: **Raleigh, Wilmington, Asheville**. If OSM is unavailable with no cached graph, the demo displays a visibly labeled fictional grid, but normal app routing and offline saving are blocked until real streets are available. Set `DEMO_ROADS=synthetic` for an entirely fictional, network-free walkthrough. Live data never falls back to invented roads or warnings.

For optional integrations, copy `.env.example` to `.env`, fill only the settings you need, and restart. Never place keys in `public/` or commit `.env`.

Follow [API setup](docs/INTEGRATIONS.md) for provider account links and exact settings. Run `node --env-file-if-exists=.env scripts/check-apis.mjs` to verify credentials and identify missing configuration without generating text/audio or writing to a database.

```powershell
Copy-Item .env.example .env
```

## Implemented workflows

- **Preparation checklist** for flood, hurricane, and extreme heat, with eight tasks per disaster and device-local completion saved for offline use. Guidance links point to Ready.gov, NHC, and NWS.
- **Upcoming-alert panel** preserves NWS onset, effective, end, and expiry separately. Shows expected start countdowns, certainty, instructions, and regional coverage; unknown timing and stale/offline snapshots are disclosed. Demo timings are fictional. New issued alerts produce an in-app notice while open; background push notifications and independent future disaster prediction are not implemented.

- Interactive **Leaflet map** focused on one selected disaster, your origin, and suggested destinations. Switching disasters clears the previous route; changing city or mode clears the origin too.
- **NOAA/NWS** alerts and station observations. Expired and test alerts are excluded. Missing polygons can be resolved from official affected zones; missing areas stay explicitly unavailable.
- **USGS** gage-height observations and historical trends. The server polls every 60 seconds and uses **Server-Sent Events** to update the browser. Observations retain their actual sensor timestamps; polling does not fabricate new observations.
- **OpenStreetMap / Overpass** roads and facilities, including hospitals, clinics, libraries, community facilities, and fire stations. No facility is declared an open shelter or cooling center without verification.
- **Census ACS** optional county population context with margin of error and vintage. A county total is never displayed as the number exposed to a hazard.
- **Destination suggestions** compare eligible resources outside checked warnings and return up to three reachable alternatives ranked by least mapped hazard exposure, then road distance. You can also choose a specific resource.
- **Shortest-route comparison** uses the same start and destination road access points and retains one-way driving restrictions. Origins and destinations both connect to road segments; destination access no longer jumps to a distant intersection. It removes hazard restrictions only for an explanatory baseline. Excluded sections appear red when the comparison overlay is enabled. The baseline cannot be selected or exported as the suggested route. Identical results are disclosed rather than claiming an improvement.
- **Risk-weighted graph routing** with one-way roads, exclusions for major flood-warning footprints, and higher costs for wind and heat exposure. Live plans also check other known warnings, while the map remains focused on the selected disaster. Reports create temporary local exclusions. Routes cannot re-enter the selected warning after exiting. This is a planning prototype, not evacuation navigation.
- Clear results for **no warning, no usable exit, no outside destination, missing boundaries, and stale data**. An origin outside the warning gets a resource-access route without implying a need to evacuate. Demo flood exits have an explicitly fictional dry corridor that is never permitted for live data.
- **Local hazard reports** with validated coordinates, explicit unverified status, six-hour expiry, and isolated demo/live storage.
- **Offline city maps** in IndexedDB plus a service worker: complete Protomaps vector basemaps (streets, buildings, water, parks, labels), saved road graph, resources, conditions, and browser-side route calculation. The same local Leaflet renderer displays the basemap online and offline. Save offline verifies the full archive size and SHA-256 hash, checks the offline app shell, and commits map + routing pack in one transaction before confirming readiness. Raleigh is 16.5 MiB, Wilmington 6.7 MiB, Asheville 7.6 MiB, plus routing data. Map bounds include a margin around the routing area; native detail goes through zoom 15 and is enlarged at higher zooms. Live offline routing still requires a complete warning snapshot less than 30 minutes old. Old packs without a basemap fall back to the saved road graph and request a new download. Browser storage can be evicted or cleared; persistence is requested on a best-effort basis. No public OSM tile-server bulk downloads are used.
- **Gemini** briefings and warning questions in English or Spanish; **ElevenLabs** narrator selection and audio; slower playback; optional record → transcribe → review → ask workflow with ElevenLabs Scribe. Provider outages use clearly labeled fallbacks. Audio recording requires browser microphone permission and only uploads when the person selects Transcribe.
- Selected-map and route GeoJSON downloads, plus briefing text downloads.

## Project structure

```text
server.mjs             HTTP API, validation, SSE, static app, request limits
lib/
  config.mjs           Region presets and polling interval
  providers.mjs        NOAA, USGS, Overpass and Census connectors
  store.mjs            SQLite cache, reports, time series and sink delivery log
  model.mjs            Normalization, exposure and action priorities
  geo.mjs              Geometry predicates, intersections and distance
  routing.mjs          OSM graph builder and risk-weighted Dijkstra search
  evacuation.mjs       Selected-disaster planning, candidate ranking and data gates
  service.mjs          Source orchestration and snapshots
  integrations.mjs     Gemini, ElevenLabs, Tiger and optional ingest bridge
  voice.mjs            Voice allowlist, bounded transcription, guidance options
  demo.mjs             Explicitly fictional, region-specific scenarios
  bundled-roads.mjs    Verified local OSM pack loader with original timestamps
  road-packs/          Bundled real routing networks for all three cities
public/
  index.html           Accessible application interface
  styles.css           Responsive desktop and mobile layout
  app.js               Map and application workflows
  offline.js           IndexedDB packs
  sw.js                Offline application shell
  vendor/              Local Leaflet 1.9.4 assets and license
sql/tiger.sql          Time-series hypertable and continuous aggregate
tests/                 Geometry, routing, provenance and HTTP tests
scripts/check-live.mjs Real provider smoke check
docs/                  Architecture, API, bonus setup and demo walkthrough
```

The supplied architecture is implemented in layers, using the existing **Node.js starter** rather than introducing a separate Python service. GeoJSON is the exchange format; the graph algorithm and geometric operations run in JavaScript and are shared with the offline browser. SQLite is built into Node 24. There is no uncalibrated ML prediction of disaster occurrence.

## Test

```sh
node --test tests/*.test.mjs
node scripts/check-live.mjs
```

Unit/API tests use an isolated temporary SQLite database and a local child server, with no paid API calls. The live smoke test requires the app server and network access. Provider downtime is reported, not replaced with simulated data.

## Bonus configuration

| Challenge | Implemented use | What you supply |
| --- | --- | --- |
| Gemini API | Evidence-grounded situation briefs | `GEMINI_API_KEY`, optional `GEMINI_MODEL` |
| ElevenLabs | Narration, stock voice selection, question transcription | `ELEVENLABS_API_KEY`, optional default `ELEVENLABS_VOICE_ID` |
| Tiger Data | Persist real observations to a hypertable; hourly continuous aggregate | `npm install`, `TIGER_DATABASE_URL` for a Tiger service |
| Applied AI streaming | Real-time interface around genuine USGS sensor observations | No key for local public sensor interface; cloud deployment still needed for a hosted dashboard |
| Databricks extension | Retryable observation batches to your ingestion bridge | A deployed HTTPS ingestion service, URL and token |

See [integration setup](docs/INTEGRATIONS.md). Bonus eligibility is determined by the organizers. No cloud deployment, paid service account, domain registration, or prize opt-in is performed automatically. Solana is intentionally omitted because this workflow does not need blockchain transactions.

## Deploy

Use the included Dockerfile on a container host with **HTTPS, persistent disk mounted at `/app/data`, and SSE support**. Keep one instance for SQLite. Example:

```sh
docker build -t wayahead .
docker run --env-file .env -e HOST=0.0.0.0 -p 4173:4173 -v wayahead-data:/app/data wayahead
```

Keys go in host secrets/environment variables. Configure a reverse proxy to preserve `Host`, disable buffering for `/api/stream`, and allow its long-lived connections. For an Internet-facing instance, add authentication and report moderation before enabling write/paid endpoints for users. The default server binds to localhost and does not authenticate users; request throttling alone is not access control.

## Scope and limitations

This is an implemented hackathon prototype. It does **not** certify safe roads, produce evacuation orders, validate shelter capacity/opening, model water depth, use elevation to predict inundation, or apply turn restrictions, vehicle dimensions, live traffic, or official road-closure feeds. Broad warning areas are conservative route exclusions, not observed flooding. A null/empty alert feed does not establish safety.

Current data connectors use NOAA/NWS, USGS, OSM/Overpass, and optionally Census. NC OneMap elevation, Census TIGER tract boundaries, EPA, NHC forecast cones, and separate DOT closure feeds are future data layers; they are not silently represented as implemented. Data.gov is a catalog rather than one uniform data API. Only APIs needed by the working workflows are queried.

Reports remain local to this server and are not sent to emergency services. Exact user GPS locations are used only on request for local route planning. AI does not select routes or change risk calculations. See [architecture and methods](docs/ARCHITECTURE.md), [API reference](docs/API.md), and [demo script](docs/DEMO.md).

Sources: [NWS API](https://www.weather.gov/documentation/services-web-api), [USGS modernization](https://api.waterdata.usgs.gov/docs/ogcapi/migration/), [Overpass QL](https://wiki.openstreetmap.org/wiki/Overpass_API/Overpass_QL), [Census ACS API](https://api.census.gov/data/2024/acs/acs5/examples.html), [Leaflet](https://leafletjs.com/). OSM data and map attribution remain visible. Leaflet's license is included in `public/vendor/LEAFLET-LICENSE`.
