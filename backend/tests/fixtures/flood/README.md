# Synthetic source fixtures

These small responses follow NWS GeoJSON alert and USGS OGC v1 latest-continuous
schemas. They are hand-authored test data, not historical/live warning claims.
All timestamps and measurement values are controlled for deterministic tests.

- `nws_active.json`: warning polygon over the existing synthetic graph's short path.
- `nws_no_geometry.json`: contextual warning with no official polygon.
- `nws_expired.json`: expired warning.
- `nws_empty.json`: successful response without active alerts.
- `nws_partial.json`: one valid alert plus an incomplete feature.
- `malformed.json`: invalid envelope.
- `usgs_observations.json`: discharge and gage-height measurements with site points.

Tests also generate invalid geometry, stale/future timestamps, cancellations,
pagination, HTTP errors, and request timeouts using mocks. No fixture is used by
the live demo, and external requests are prohibited in the flood test module.
