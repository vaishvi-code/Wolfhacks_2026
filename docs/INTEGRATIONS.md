# Bonus integrations and API setup

Core NOAA/NWS, USGS, and Overpass calls work without your own keys. Optional integrations remain visibly unconfigured until credentials are supplied. Keys are read from the server environment and never returned to the browser.

## Gemini API

1. Create a key in [Google AI Studio](https://aistudio.google.com/apikey).
2. Set `GEMINI_API_KEY` in `.env`. The configurable default model is `gemini-3.5-flash-lite`; use a model available in your account if its availability changes. Google recommends current models for new projects because 2.5 access is limited to existing users; see the [model availability notes](https://ai.google.dev/gemini-api/docs/deprecations).
3. Restart the server, select a region/mode, and open **My guidance → Explain my situation**.
4. Verify the briefing provider badge says **Gemini**. A provider error or missing key produces an explicitly labeled source template.

The server calls the [GenerateContent REST API](https://ai.google.dev/api/generate-content), using official alert text and current snapshot metadata as evidence. It sends no user GPS or free-text community reports to Gemini. The model summarizes; deterministic geometry/routing code makes map decisions. Generated text should still be checked against sources.

## ElevenLabs

1. Create a key in your [ElevenLabs account](https://elevenlabs.io/app/settings/api-keys).
2. Choose a voice ID you are authorized to use. Set `ELEVENLABS_API_KEY` and `ELEVENLABS_VOICE_ID` in `.env`.
3. Restart, generate a brief, then click **Listen**. A configured ElevenLabs service supplies the audio.

The server calls the [text-to-speech endpoint](https://elevenlabs.io/docs/api-reference/text-to-speech/convert) with `eleven_multilingual_v2`, returning MP3 audio. It only accepts IDs of server-generated briefs. No-key playback uses the browser voice and is identified by the browser-voice playback notice; that fallback does not count as an ElevenLabs integration for judging. Cloud generation may use account credits; there are no background generation calls.

## Tiger Data

1. Create a Tiger Cloud service and obtain its secure PostgreSQL connection string.
2. Run `npm install` to install the optional `pg` driver.
3. Set `TIGER_DATABASE_URL` to the TLS-enabled connection string from the service dashboard. Do not disable certificate verification to work around a configuration error.
4. Restart. The server applies `sql/tiger.sql`: an observation hypertable, an hourly continuous aggregate, and a five-minute aggregate refresh policy.
5. Open live data. Genuine USGS observations are written to `sensor_readings`, keyed by station/time. Demo observations never enter this stream.

Verification query in the Tiger SQL editor:

```sql
SELECT station_id, count(*) AS observations,
       max(observed_at) AS latest_observation
FROM sensor_readings GROUP BY station_id;

SELECT * FROM sensor_hourly ORDER BY bucket DESC LIMIT 24;
```

Hourly aggregates leave the current hour open; policy refresh runs every five minutes. The local SQLite delivery log tracks acknowledgements. Failed inserts are retried at the next poll; successful station/timestamp rows are idempotent. Historical corrections to already-acknowledged values are not comprehensively backfilled in this prototype. Local service continues if Tiger is unavailable. See [Tiger documentation](https://www.tigerdata.com/docs).

## Applied AI Data Streaming challenge

The implemented pipeline is **USGS public sensor → server poll → normalized observation → SQLite → SSE → live browser dashboard**. A 60-second request interval does not change the upstream measurement interval. The Data & help panel shows the latest supporting observation and its actual timestamp. The main screen remains focused on personal evacuation planning. You can demonstrate it without Databricks by using the challenge's real-time IoT interface route, subject to organizer interpretation. Host the dashboard to meet a cloud-dashboard requirement; local execution alone is not a hosted deployment.

An optional `DATABRICKS_INGEST_URL` hook forwards real observation batches to **a service you deploy**, with optional bearer token `DATABRICKS_INGEST_TOKEN`. It is not a native Databricks API endpoint, and setting a workspace URL here is insufficient. The app does not claim that data reached Databricks merely because the hook is configured.

Bridge request contract:

```json
{
  "source":"USGS",
  "observations":[{
    "event_id":"USGS-02087500:2026-10-03T20:15:00.000Z",
    "station_id":"02087500",
    "observed_at":"2026-10-03T20:15:00.000Z",
    "gage_height_ft":1.18,
    "provisional":true
  }]
}
```

Your bridge must authenticate the caller, validate this schema, durably enqueue or write the records into your Databricks ingestion architecture, deduplicate by `event_id`, and only then return a 2xx response. TerraWatch retries failed deliveries from its local log. A network timeout after acknowledgement can resend a batch, so receiver-side deduplication is required. No generic Databricks delivery is claimed or tested without that bridge.

## Census ACS

Set `CENSUS_API_KEY` to enable county context. The [Census examples](https://api.census.gov/data/2024/acs/acs5/examples.html) currently require API keys. Queries use `B01003_001E` (total population) and `B01003_001M` (margin of error) from the 2024 five-year ACS. No affected-population estimate is inferred from whole-county totals.

## Prize submission scope

The strongest fit is geospatial response planning plus Gemini, ElevenLabs, and the public sensor stream. Tiger adds meaningful time-series persistence when configured. Solana transactions and domain purchases are outside the implemented workflow. Select the desired prize categories in the hackathon Team tab yourself, and demonstrate the actual configured integrations rather than their fallbacks.
