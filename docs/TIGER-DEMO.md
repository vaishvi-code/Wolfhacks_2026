# Tiger conditions and route updates

Tiger is queried by the app, not just used as a data sink. `sensor_readings`
stores real USGS observations; `sensor_hourly` is a continuous aggregate with
real-time aggregation enabled. `road_events` is a second hypertable containing
timestamped, located road reports, status, expiry, region, mode, and simulation
flags. Live and demo events are separated in every query.

The conditions panel shows a fresh river trend only when real observations have
enough history around a one-hour interval. It marks stale and insufficient data
explicitly. Hourly history is available inside What changed. A gauge rise is
context only and never creates a road closure or inundation polygon.

Road events are written through a SQLite outbox and retried idempotently in
Tiger. Active Tiger events are read back into the routing snapshot. A new road
report within 75 m of a previous route clears that route and offers
recalculation. Reports remain unverified. Route exclusions use the same 75 m
rule as the route-change alert.

## Five-minute demo

1. Select Try a demo, use an example location, and Find a route.
2. In Conditions & route updates, check that the source badge says Tiger Data.
3. Select Demo: add a closure to my route. The server chooses an interior point
   on the current calculated route, rather than trusting a supplied path.
4. The simulated event is inserted into Tiger and read back. The previous route
   is cleared and an explanatory route-change alert appears.
5. Select Recalculate route. Routing now excludes roads near that event. An
   alternative route is shown if reachable; otherwise the app reports no route.
6. Expand What changed to show the timestamped event. Clear demo closures
   resolves these simulated events and allows the demonstration to be repeated.

Simulation lasts ten minutes and cannot be inserted through the demo endpoint
in live mode. No synthetic sensor values are inserted into the live sensor
table. User origins and full route histories are not sent to Tiger by this
feature; the explanation of the previous route exists in the current browser
session. The event history persists in Tiger.

When Tiger is unconfigured, fails, or takes longer than 2.5 seconds to answer,
the UI explicitly labels the local fallback. Local events remain usable and
delivery can retry. Never present that fallback as a verified Tiger demo.

Save offline includes the conditions snapshot and active road events. Offline
view displays Saved snapshot and says new conditions will not arrive. Demo
injection and reset require connectivity. Live warnings still have their
existing freshness checks.

Set TIGER_DATABASE_URL in the server environment and install the existing `pg`
optional dependency. App startup initializes sql/tiger.sql. Deployed schema
changes take effect on server restart. The database connection stays on the
server; no credentials are shipped to the browser.
