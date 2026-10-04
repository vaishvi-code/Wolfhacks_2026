# Three-minute personal evacuation demo

**Before judging:** start the server, load Raleigh Live conditions once to warm caches, and check Data & help. Configure and exercise any bonus services you intend to claim. Return to Try a demo; only one fictional disaster is shown at a time.

1. **The person — 20 seconds.** “I'm inside a disaster warning and need to find a way out. WayAhead starts with my situation and my location.” Point out **Real streets. Simulated disaster.** Roads and resource locations come from OSM; the disaster conditions are invented for testing.
2. **Flood exit — 45 seconds.** Choose Flood, Use example location, then Find my exit route. The starting point is inside the simulated warning. The result follows named OSM streets to a mapped resource outside it. Show **Why this route?**: both road distance and caution exposure are compared with the shortest route to that same resource. Click **Show shortest on map**; gray dashes show the comparison and red dashes identify excluded segments, if any. Choose on map also accepts custom starting points. Red flooded patches prevent access. Dry-road assumptions are simulated and never apply to live warnings.
3. **One disaster at a time — 30 seconds.** Select Hurricane. The flood route disappears. Choose Use example location again and find a route. Repeat with Extreme heat. Names and distances depend on the actual road graph and chosen origin. Suggestions are ranked using warning-exposure costs, not distance alone. Opening, shelter, and cooling status remain unverified. Some shortest and chosen routes are identical; the app says so.
4. **A clear explanation — 25 seconds.** Open My guidance, then Explain my situation. Only the selected disaster is explained. Configured Gemini and ElevenLabs are available here; otherwise the UI identifies Local template and browser voice accurately.
5. **Real conditions — 30 seconds.** Choose Live conditions and open Data & help. Show real NOAA, OSM and USGS source timestamps. No matching warning is a legitimate outcome: the app does not substitute a demo or declare conditions safe. Use your location only with permission, or enter a chosen point in the coverage area.
6. **Blocked exits and resilience — 30 seconds.** Explain that nearby road reports create temporary exclusions and may lead to no route. Save offline preserves warnings, resources, and graph routing in this browser. Warning polygons and the computed route remain available without the server; basemap tiles are not part of the download.

## Match the judging criteria

- **Track:** geographic warning boundaries determine which destinations are eligible and which road segments are excluded or penalized.
- **Technology:** public-data connectors, spatial intersection, one graph search across destination candidates, SSE, SQLite, shared offline routing, optional AI/audio/time-series integrations.
- **Design:** one disaster, explicit location, suggested resources first, focused map, accessible textual result, clear no-route outcomes.
- **Execution:** demonstrate a calculated route from inside each scenario, destination alternatives, selected-disaster guidance, actual source status, and offline routing.

## Be explicit about scope

City coverage is limited; this is not a statewide evacuation navigator. The app cannot certify safe roads or open shelters. Warning footprints do not measure flooding, and river-gage height is not flood stage. Missing boundaries, stale feeds, or no usable exit can prevent a route. Follow official emergency directions. Do not claim any optional provider is connected unless it is configured and verified.
