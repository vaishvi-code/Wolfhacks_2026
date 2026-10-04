# Bundled city basemaps

These PMTiles v3 archives are geographic extracts of Protomaps' 2026-10-03
OpenStreetMap/Natural Earth basemap build (tileset schema 4.15.2), with native
zoom levels 0–15. Coverage extends 0.05 degrees beyond each configured routing
boundary. Map coverage does not expand the road graph used for routing.

Source: https://build.protomaps.com/20261003.pmtiles
Download documentation: https://docs.protomaps.com/basemaps/downloads
Attribution: © OpenStreetMap contributors · Protomaps
Copyright information: https://www.openstreetmap.org/copyright
Protomaps distributes the basemap as an ODbL Produced Work. Keep attribution
visible when displaying or redistributing these maps.

The app downloads a whole selected city archive into IndexedDB on Save offline.
Normal online display uses HTTP byte ranges, served by the Node application.
Archives are intentionally excluded from service-worker runtime caching to
avoid confusing partial responses with complete offline downloads.

## Updating maps

Install the official go-pmtiles CLI from
https://github.com/protomaps/go-pmtiles/releases . Run from the repository root:

```
node scripts/build-offline-maps.mjs
```

Set `PMTILES_CLI` to an executable path if it is not on PATH. Set `MAP_BUILD`
to an available YYYYMMDD Protomaps build date to refresh the data. The script
extracts all three cities and regenerates `catalog.json` with sizes and SHA-256
hashes. Commit the archives and catalog together. They are included by the
existing Dockerfile and require no runtime map subscription or API key.

Renderer dependencies are pinned and served from `public/vendor`:
protomaps-leaflet 5.0.0 and pmtiles 3.2.1, with their license files alongside.
The Leaflet renderer is in maintenance mode and is used here because the
existing application depends on Leaflet controls and overlays.

## Phone verification

1. Open the HTTPS app and use Save offline for the selected city and data mode.
2. Wait for Available offline; a failure or interrupted download is not ready.
3. Close the app, enable airplane mode, and reopen it.
4. Pan and zoom inside the saved city. Buildings and street labels should remain.
5. Use a demo origin to test routing on the saved real street graph. Real warning
   routing requires a fresh saved live snapshot and may legitimately find no route.

Browsers can delete site storage. Re-save after clearing browser data or if the
app reports a missing map. Warning and road conditions are snapshots, not live
updates while disconnected.
