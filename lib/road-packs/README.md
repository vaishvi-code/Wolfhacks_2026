# Bundled OpenStreetMap routing data

Real road networks and mapped resources for the configured Raleigh, Wilmington,
and Asheville coverage areas, stored as gzipped JSON. These are separate from
the PMTiles background maps. See manifest.json for original download dates,
coverage, counts, archive sizes, and SHA-256 integrity hashes.

Data © OpenStreetMap contributors, licensed under ODbL:
https://www.openstreetmap.org/copyright
https://opendatacommons.org/licenses/odbl/1-0/

Demo mode loads these real streets directly without contacting Overpass. Only
the demo disaster footprints and road-condition assumptions are fictional.
Live mode can use a fresh bundle for base road geometry. Older bundles are
marked stale; loading them never changes their original download dates. Live
warning and road freshness checks are retained. These files do not establish
road passability, shelter status, opening hours, capacity, or current closures.

To refresh all three from public Overpass data, run from the repository root:

```
node scripts/build-road-packs.mjs
```

The build command deliberately bypasses existing bundles. An optional
`--use-local-cache` flag uses the existing public OSM SQLite cache where present,
preserving each graph's original fetchedAt. All archives and manifest must be
committed and deployed together. The existing Dockerfile copies them under lib.
Never set DEMO_ROADS=synthetic on the deployed app; that option is for tests.
