# Bonus integrations and API setup

Core NOAA/NWS, USGS, and Overpass calls work without your own keys. Optional integrations remain visibly unconfigured until credentials are supplied. Keys are read from the server environment and never returned to the browser.

## Local setup sequence

The local `.env` file is ignored by Git. If you are setting up a fresh checkout, copy `.env.example` to `.env` once; do not overwrite an existing file containing keys. Add the value after each `=` and save. Use quotes around connection strings or other values containing `#` or spaces. Keep API keys in `.env`, not `.env.example`.

| Service | Open this page | Settings in `.env` |
| --- | --- | --- |
| Gemini | [Google AI Studio API keys](https://aistudio.google.com/apikey) | `GEMINI_API_KEY`, `GEMINI_MODEL` |
| ElevenLabs | [ElevenLabs API keys](https://elevenlabs.io/app/settings/api-keys) | `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` |
| Tiger Data | [Tiger Cloud console](https://console.cloud.timescale.com/) | `TIGER_DATABASE_URL` |
| Census | [Request a Census key](https://api.census.gov/data/key_signup.html) | `CENSUS_API_KEY` |

Sign in to each provider yourself and review its terms, access permissions, quotas, and any plan charges shown before completing setup. After saving settings, run this from the project folder:

```powershell
node --env-file-if-exists=.env scripts/check-apis.mjs
```

With npm available, `npm run check:apis` does the same thing. The checker reads real metadata and database state without generating text/audio, sending ingest batches, or changing the database. It never prints credentials or raw provider error bodies. `verified-access` confirms only the tested metadata/database access; it does not claim generation quota, speech permission, or live sensor delivery was tested. `needs-key` and `needs-setting` identify remaining setup. Exit codes are 0 for passed applicable checks, 1 for failed checks, and 2 for incomplete setup.

Restart the app after changing `.env`; the running server does not automatically reload secrets. Then exercise **My guidance → Explain my situation → Listen** to test Gemini and ElevenLabs. Those deliberate generation calls may consume provider credits. For Tiger Data, use **Live conditions → Data & help** and verify stored USGS records with the SQL query below.

## Gemini API

1. Create a key in [Google AI Studio](https://aistudio.google.com/apikey).
2. Set `GEMINI_API_KEY` in `.env`. The configurable default model is `gemini-3.5-flash-lite`; use a model available in your account if its availability changes. Google recommends current models for new projects because 2.5 access is limited to existing users; see the [model availability notes](https://ai.google.dev/gemini-api/docs/deprecations).
3. Restart the server, select a region/mode, and open **My guidance → Explain my situation**.
4. Verify the briefing provider badge says **Gemini**. A provider error or missing key produces an explicitly labeled source template.

The server calls the [GenerateContent REST API](https://ai.google.dev/api/generate-content), using official alert text and current snapshot metadata as evidence. It sends no user GPS or free-text community reports to Gemini. The model summarizes; deterministic geometry/routing code makes map decisions. Generated text should still be checked against sources.

## ElevenLabs

1. Create a key in your [ElevenLabs account](https://elevenlabs.io/app/settings/api-keys).
2. Set `ELEVENLABS_API_KEY` in `.env`. Optionally set `ELEVENLABS_VOICE_ID` to an authorized default voice; otherwise choose a stock narrator in **My guidance**.
3. Restart, select English or Spanish and a narrator, generate a brief, then click **Listen**. A configured ElevenLabs service supplies the audio. Slower playback uses the browser's playback speed control without generating another clip.

The server calls the [text-to-speech endpoint](https://elevenlabs.io/docs/api-reference/text-to-speech/convert) with `eleven_multilingual_v2`, returning MP3 audio. It only accepts IDs of server-generated briefs. No-key playback uses the browser voice and is identified by the browser-voice playback notice; that fallback does not count as an ElevenLabs integration for judging. Cloud generation may use account credits; there are no background generation calls.

When restricting the key, allow **Text to Speech: Access**, **Speech to Text: Access**, and **Voices: Read**. Leave all other endpoints at No Access, retain automatic disabling for leaked keys, and choose an expiry that lasts through your demo. Set a credit limit appropriate to your provider plan. Copy a default voice's ID, not its display name or web address. The checker uses voice metadata only; a successful lookup alone does not prove speech-generation or transcription permission.

**Questions and languages:** Gemini generates the English or Spanish text; ElevenLabs narrates it with `eleven_multilingual_v2`. The voice selector lists stock voices from [GET /v2/voices](https://elevenlabs.io/docs/api-reference/voices/search), with a five-minute server cache. It does not create or clone voices. If Gemini is unavailable, the app explicitly labels the English template and explains that it does not answer the submitted question.

In **My guidance → Ask about this warning**, type a question or record up to 30 seconds. Recording stays in browser memory until **Transcribe recording** uploads it to ElevenLabs [Scribe v2](https://elevenlabs.io/docs/api-reference/speech-to-text/convert). Review/edit the transcript before **Ask about this warning** sends the question and selected warning evidence to Gemini. The app does not write recordings or transcripts to its database; provider retention policies still apply. Closing the dialog stops the microphone and discards its recording. The server accepts at most 2 MB of WebM, Ogg, MP4, WAV or MP3 audio and rate-limits transcription. Questions are limited to 600 characters. AI receives no calculated route and cannot certify road safety or shelter availability.

These controls do not require ElevenAgents, Dubbing, Models, Voice Generation, or workspace administration permissions. Microphone recording requires HTTPS or localhost and browser support; typed questions remain available when recording is unsupported. Speech to Text and Text to Speech consume provider credits only when explicitly requested.

## Tiger Data

1. Create a Tiger Cloud service and obtain its secure PostgreSQL connection string.
2. Run `npm install` to install the optional `pg` driver.
3. Set `TIGER_DATABASE_URL` to the TLS-enabled connection string from the service dashboard. For Tiger's **Shared Free** service, append `sslmode=no-verify`: the free service uses a self-signed certificate and does not provide a CA certificate. This keeps traffic encrypted but does not verify the server identity. Use `sslmode=verify-full` or `sslmode=verify-ca` after moving to a service with a signed certificate and configured CA chain. Do not use `ssl=false`.
4. Restart. The server applies `sql/tiger.sql`: an observation hypertable, an hourly continuous aggregate, and a five-minute aggregate refresh policy.
5. Open live data. Genuine USGS observations are written to `sensor_readings`, keyed by station/time. Demo observations never enter this stream.

Use the **database password**, not the Tiger website password. Include it in the PostgreSQL URL if the console leaves a password placeholder. For Shared Free, retain `sslmode=no-verify`; for a signed service, use `sslmode=verify-full` or `sslmode=verify-ca` with the provider CA. Never use `ssl=false`. The app initializes its own tables and aggregate in the selected database, so use a service/database intended for this project. See [finding connection details](https://docs.tigerdata.com/use-timescale/latest/integrations/find-connection-details/).

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

Your bridge must authenticate the caller, validate this schema, durably enqueue or write the records into your Databricks ingestion architecture, deduplicate by `event_id`, and only then return a 2xx response. WayAhead retries failed deliveries from its local log. A network timeout after acknowledgement can resend a batch, so receiver-side deduplication is required. No generic Databricks delivery is claimed or tested without that bridge.

## Census ACS

Set `CENSUS_API_KEY` to enable county context. The [Census examples](https://api.census.gov/data/2024/acs/acs5/examples.html) currently require API keys. Queries use `B01003_001E` (total population) and `B01003_001M` (margin of error) from the 2024 five-year ACS. No affected-population estimate is inferred from whole-county totals.

Request the key from the [official signup form](https://api.census.gov/data/key_signup.html), then follow the activation instructions sent by Census before testing it.

Controlled county population estimates can carry the special margin-of-error code `-555555555`. The app keeps the valid population, stores `marginOfError: null`, and labels it `controlled`, rather than rejecting it or reporting a negative/zero error. Suppressed population counts remain unavailable. See the [Census annotation definitions](https://www.census.gov/data/developers/data-sets/acs-1year/notes-on-acs-estimate-and-annotation-values.html).

## Other track datasets and prize tools

**Area context:** Open Updates → Load area data to query the selected city's bounding box. The reference map has independent toggles for NC OneMap / NCEM effective special flood hazard polygons, Census TIGERweb tract boundaries, and EPA ECHO facility locations. TIGERweb is Census geographic data; the app does not download TIGER/Line shapefiles. These reference layers do not change routing exclusions or establish current inundation, population exposure, contamination, or road passability. Query results are capped at 200 flood polygons, 100 tracts, and 100 EPA facilities; capped results are labeled partial. Responses are cached for 24 hours, and unavailable sources retain their dated cached data when available. Queries run only when requested, so they do not delay initial planner loading.

After loading Area context, chat receives bounded summaries for that city: flood-zone categories, tract names and GEOIDs, facility names, source status, retrieval times and catalog results. Geometry and credentials are not sent to Gemini. Reference data is real-city background even in demo mode. The chat identifies when area context has not been loaded.

**Data.gov:** Dataset discovery uses the current [Catalog API](https://resources.data.gov/catalog-api/), not the retired CKAN interface. Set `DATA_GOV_API_KEY` in the server environment to enable spatially filtered flood-dataset search. Without a key, the UI offers a catalog link and explicitly shows discovery as unconfigured. Catalog results contain metadata and source links, not actual measurements. No API key is exposed to the browser.

Run `node --env-file-if-exists=.env scripts/check-area-context.mjs raleigh` (or `wilmington` / `asheville`) to check source statuses and feature counts without printing credentials. An optional second argument caches verified results in a local SQLite file for previewing.

Solana wallet transactions and GoDaddy domain registration are separate product/deployment work, not missing keys for the evacuation planner. The Databricks hook above also needs its deployed ingestion service; do not enter a workspace URL and treat it as connected.

## Prize submission scope

The strongest fit is geospatial response planning plus Gemini, ElevenLabs, and the public sensor stream. Tiger adds meaningful time-series persistence when configured. Solana transactions and domain purchases are outside the implemented workflow. Select the desired prize categories in the hackathon Team tab yourself, and demonstrate the actual configured integrations rather than their fallbacks.
