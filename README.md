# Wayfinder

A Wolfhacks 2026 prototype for the **Center for Geospatial Analytics** track, with a near-real-time USGS sensor dashboard for the **Applied AI Data Streaming Challenge**.

## Run

Python 3.9+; no third-party packages, API keys, or build step required.

```sh
python3 server.py
```

Open http://localhost:8000. Run verification with `python3 -m unittest discover -s tests -v`.

## What works

- Geographic sample road graph in Raleigh, hazard buffers, and selectable start/destination.
- Weighted Dijkstra routing: clear ×1, caution ×3, high risk ×12; blocked edges removed. Automatic destination minimizes weighted distance, not travel time. No viable route produces an explicit error.
- Community reports persisted in SQLite, applied to nearby segments, and expired after six hours. Reports remain unverified.
- NWS point alerts on demand, with fetch timestamps and cached fallback. Alerts supply context; they do not infer road passability.
- USGS river-gauge ingestion every 60 seconds, timestamp deduplication, two-day retained observations, trend charts, observation age, and explicit upstream failures. Browser checks the ingestion store every 10 seconds. Observations older than 90 minutes are labeled stale. No synthetic sensor readings are used.
- Cached app shell and last sample scenario for offline viewing/routing after a successful first visit. Reports require the server. Optional web fonts fall back to system fonts offline.

## Demo boundaries

The road graph, hazard scenario, illustrated waterway, and destinations are **synthetic** at Raleigh coordinates. They do not represent verified roads or operating shelters. This is not a navigation or emergency service. Live NWS/USGS information is separate from the sample route calculation; a gauge measurement alone cannot determine road passability. USGS observations are provisional. GPS only snaps positions within the sample extent to a sample node.

The attachment's broader design (OSM roads, verified shelters, elevation, flood/hurricane/heat models) is a roadmap. Replace sample geometry with a verified road graph and validate hazard-to-road mapping before real-world use.

## Sensor pipeline and bonus challenge

USGS physical gauges → public instantaneous-values API → Python ingestion → SQLite timestamped observation store → water-level trend analytics → browser dashboard. Stations: 02087500, 02087183, 02087359. Station availability and reporting intervals vary. This is polling-based near-real-time telemetry, not a Databricks stream or team-built sensor device. No ML model is included. Confirm with organizers that public telemetry satisfies their IoT category before claiming eligibility.

Official sources:
- [USGS instantaneous-values documentation](https://waterservices.usgs.gov/docs/instantaneous-values/)
- [Neuse River near Clayton station](https://waterdata.usgs.gov/monitoring-location/USGS-02087500/)
- [NWS API documentation](https://www.weather.gov/documentation/services-web-api)

USGS plans to retire legacy WaterServices APIs in early 2027; migrate ingestion to api.waterdata.usgs.gov for continued use.

## Cloud deployment

A Dockerfile is included. On a Python/container host use `HOST=0.0.0.0`, the platform-provided `PORT`, and `python server.py` as the start command. Mount a persistent volume at `/app/data` for SQLite history and reports. Use HTTPS for service worker/GPS support outside localhost. Set `NWS_USER_AGENT` to an application identifier with a real contact before public deployment.

The app is currently local; no cloud deployment or team registration has been performed. The standard-library HTTP server is a hackathon demo server. Public deployment needs a production HTTP server, moderation/authentication, rate limits, and a retention policy review. Choose the Geospatial track and opt into the bonus through the event's Team tab.

## Demo sequence

1. Start at grid 1-2; select North community hub and calculate the route.
2. Report a blocked road on a route node and recalculate to show rerouting.
3. Open Live sensor feed to show real observations, source times, trends, and pipeline status.
4. Refresh NWS conditions. Stop the server and reload to demonstrate cached sample routing.
