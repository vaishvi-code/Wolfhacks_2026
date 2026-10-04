-- Run against a Tiger Cloud service with TimescaleDB enabled.
CREATE TABLE IF NOT EXISTS sensor_readings (
  observed_at TIMESTAMPTZ NOT NULL,
  station_id TEXT NOT NULL,
  gage_height_ft DOUBLE PRECISION NOT NULL,
  provisional BOOLEAN NOT NULL,
  PRIMARY KEY (station_id, observed_at)
);
SELECT create_hypertable('sensor_readings', 'observed_at', if_not_exists => TRUE);
CREATE MATERIALIZED VIEW IF NOT EXISTS sensor_hourly
WITH (timescaledb.continuous) AS
SELECT time_bucket(INTERVAL '1 hour', observed_at) AS bucket,
       station_id, AVG(gage_height_ft) AS average_ft,
       MAX(gage_height_ft) AS max_ft, MIN(gage_height_ft) AS min_ft,
       COUNT(*) AS observations
FROM sensor_readings GROUP BY bucket, station_id
WITH NO DATA;
SELECT add_continuous_aggregate_policy('sensor_hourly',
  start_offset => INTERVAL '7 days', end_offset => INTERVAL '1 hour',
  schedule_interval => INTERVAL '5 minutes', if_not_exists => TRUE);
-- Include the newest raw observations between background refreshes.
ALTER MATERIALIZED VIEW sensor_hourly SET (timescaledb.materialized_only = false);

CREATE TABLE IF NOT EXISTS road_events (
  event_id TEXT NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  region TEXT NOT NULL,
  mode TEXT NOT NULL CHECK (mode IN ('live','demo')),
  kind TEXT NOT NULL,
  longitude DOUBLE PRECISION NOT NULL,
  latitude DOUBLE PRECISION NOT NULL,
  description TEXT NOT NULL,
  status TEXT NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  simulation BOOLEAN NOT NULL DEFAULT false,
  PRIMARY KEY (event_id, occurred_at)
);
SELECT create_hypertable('road_events', 'occurred_at', if_not_exists => TRUE);
CREATE INDEX IF NOT EXISTS road_events_region_mode_time ON road_events (region,mode,occurred_at DESC);
