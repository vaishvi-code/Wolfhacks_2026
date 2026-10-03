# Synthetic tropical cyclone fixtures

These are hand-authored official-schema examples, not real alerts or storm claims.
The geometry intersects the existing synthetic graph's edge 2 → 3. Timestamps
are fixed at 2026-10-03 for deterministic tests.

- `nws_warning.json`: Hurricane Warning polygon with optional storm parameters.
- `nws_events.json`: all six requested warning/watch events, contextual/defensive
  tropical events, and unrelated alerts that must be filtered out.
- `nws_no_geometry.json`: zone-referenced alert without a supplied polygon.
- `nws_expired.json`: expired warning.
- `nws_partial.json`: valid warning plus malformed alert records.
- `nws_empty.json`: successful empty collection.
- `nws_duplicate.json`: repeated equivalent official alert ID.
- `nhc_storms.json`: NHC `activeStorms` layout, synthetic storm center/intensity and
  product references; no road hazard footprint. Links are deliberately synthetic
  and never fetched by tests.

Schema references: [NWS API](https://www.weather.gov/documentation/services-web-api)
and [official NHC sample](https://www.nhc.noaa.gov/productexamples/NHC_JSON_Sample.json).
Tests mock timeouts/partial pagination, stale/cancelled records and failed sources.
HTTP and socket connections are prohibited by the test module.
