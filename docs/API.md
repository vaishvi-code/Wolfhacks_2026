# HTTP API

Default origin: `http://127.0.0.1:4173`. JSON writes require `Content-Type: application/json`. Supported `region`: `raleigh`, `wilmington`, `asheville`. Supported `mode`: `live`, `demo` (default API mode is live; the UI opens in demo).

| Method | Endpoint | Result |
| --- | --- | --- |
| GET | `/api/health` | Server health and timestamp |
| GET | `/api/config` | Regions, polling interval, integration status; never secrets |
| GET | `/api/snapshot?region=raleigh&mode=live` | Hazards, resources, sensor readings, source status, priorities, reports, graph counts |
| POST | `/api/refresh` | Refresh due sources; same snapshot schema |
| GET | `/api/offline?region=raleigh&mode=demo` | Snapshot including graph nodes and edges |
| GET | `/api/stream?region=raleigh` | SSE `connected` and live `snapshot` events |
| POST | `/api/reports` | Store an unverified observation; HTTP 201 |
| POST | `/api/route` | Route geometry, weighted cost, distance, exclusions, caveats |
| POST | `/api/evacuate` | Personal evacuation result with up to three ranked destination/route alternatives |
| POST | `/api/brief` | Gemini or local template brief with server-issued ID |
| POST | `/api/audio` | ElevenLabs MP3 for a generated brief ID |
| POST | `/api/voices` | Stock narrator IDs and names; JSON body `{}`; cached for five minutes |
| POST | `/api/transcribe?language=en` | Raw audio body → ElevenLabs transcript for user review |

Refresh/brief body:

```json
{"region":"raleigh","mode":"demo"}
```

Report body (coordinates are **longitude, latitude**):

```json
{
  "region":"raleigh",
  "mode":"demo",
  "kind":"blocked_road",
  "coordinates":[-78.6742,35.7676],
  "description":"Scenario observation: tree branches across the road."
}
```

`kind` is `flooded_road`, `blocked_road`, `fallen_tree`, or `heat_concern`. Description must be 5–600 characters. Coordinates must fall inside the selected city window. Reports expire after six hours and stay within their mode and region.

Personal evacuation body (used by the UI):

```json
{
  "region":"raleigh",
  "mode":"demo",
  "hazard":"flood",
  "start":[-78.6262,35.7796],
  "destinationId":null
}
```

`hazard` must be `flood`, `hurricane`, or `heat`. Omit `destinationId` or send `null` to automatically compare resources. An ID restricts the search to that eligible destination. No origin is inferred. Coordinates outside the coverage area return HTTP 400.

HTTP 200 returns a domain `status`: `routes_found`, `no_warning`, `no_route`, `no_destination`, `destination_unavailable`, `incomplete_data`, or `stale_data`. Only `routes_found` has alternatives. Each alternative includes road geometry, distance, weighted cost, caution distance, access snap distances, destination, and caveats. All resources and roads remain unverified.

Each alternative also includes `comparison.status`. When available, `comparison.shortest` contains `comparisonOnly: true`, geometry, distance, exposure distance, excluded distance, and excluded segment geometry. `samePath` and `extraDistanceKm` describe the difference from the selected route. The shortest baseline preserves the same road access points and one-way rules but ignores hazard restrictions; it is not another evacuation recommendation.

`mapSnapshot` contains the exact snapshot used for the calculation, excluding the large road graph. The UI applies it with the result, so a recovered source or refreshed warning cannot leave the old map and provenance beside a newer route.

Demo snapshots identify the road geometry with `roadInfo.basis` and `demoRouting.basis`: `osm` means mapped streets and resources with simulated conditions; `synthetic` means an entirely fictional grid and resources. `demoRouting.fallbackReason`, when present, explains an OSM outage. OSM source timestamps remain the actual retrieval times. The prepared demo graph is cached for 60 seconds to avoid repeating provider timeouts on each route request.

Demo scenarios check only the selected disaster. Live routes check all active supported warnings even though the map shows the selected type. A live origin inside excluded flood segments may have no usable exit. No straight-line route is fabricated. Destinations and their road access points must be outside checked warnings. No warning returned does not imply safe conditions.

Live routing requires successful NWS data within 15 minutes and OSM data within 25 hours, with all relevant warning boundaries mapped. Offline live packs must also be younger than 30 minutes. The browser clears a live plan after 15 minutes, or earlier when relevant conditions change.

Send `"hazard":"flood"` (or another supported category) to `/api/brief` for selected-disaster guidance. The summary does not receive the user's precise starting coordinates.

Briefing also accepts `"language":"en"` or `"es"` (default English) and an optional `"question"` of at most 600 characters. The question and selected warning evidence go to Gemini. The response includes `language` for the actual output; a fallback is explicitly labeled English and does not claim to answer the question.

Legacy point-to-resource route body:

```json
{
  "region":"raleigh",
  "mode":"demo",
  "start":[-78.6742,35.7676],
  "destinationId":"ID_FROM_SNAPSHOT_FACILITIES"
}
```

The legacy `/api/route` endpoint uses all hazards and is not used by the personal planner UI. HTTP 422 means there is no connected route under the supplied constraints or the point cannot be snapped. HTTP 409 means necessary live warning/road coverage is incomplete. A successful response does not certify route safety.

Audio body:

```json
{"region":"raleigh","mode":"demo","briefId":"ID_FROM_BRIEF_RESPONSE","voiceId":"ID_FROM_VOICES_RESPONSE"}
```

No arbitrary speech text is accepted. Brief IDs expire after an hour; audio requires configured ElevenLabs credentials. Optional-provider failures never fabricate output. Briefing may return a source template with the error explained in `notice`.

The narrator must be returned by `/api/voices` or match the server's configured default. Omit `voiceId` to use `ELEVENLABS_VOICE_ID` when set. Stock voice names are public metadata; private samples and other account fields are not returned.

Transcription is the only non-JSON write: send raw bytes with `Content-Type: audio/webm`, `audio/ogg`, `audio/mp4`, `audio/wav`, or `audio/mpeg`. Bodies over 2 MB return HTTP 413; unsupported formats return 415. The UI limits recordings to 30 seconds. Language is `en` or `es`. The response is `{text, provider, language, truncated}`; text is limited to 600 characters. Recording and transcript are not written to the app database. Transcription does not call Gemini or change a route: users review text and explicitly submit a subsequent brief/question request.

Errors use `{"error":"Readable explanation"}`. Writes are limited per client address and endpoint per minute (reports 8, briefing/audio/transcription/voice listing 6, other writes 30). This is a local abuse guard, not a replacement for authentication.
