# Phase 3 corrective review

## Resumed November 7 — browser blocker resolved, local verification green

The rendering blocker is resolved and **both browser suites pass** on the
production build, followed by a green `.\scripts\verify_local.ps1 -Browser`
(74 Python tests in 63.88 s, loopback startup 6.24 s, TypeScript/Vite build,
real-data smoke and filter races; no JavaScript errors). Data-audit code did not
change, so the independent audit was not rerun. Nothing was merged, deployed,
or run in remote CI.

**Root cause (verified in MapLibre 6.16.0 source and by probing a headless run).**
Two coupled defects came from gating layer visibility on `map.isSourceLoaded()`:

1. `Style.update()` decides which sources upload tile buffers (`tileManager.used`)
   from layer visibility, then synchronously fires a `sourcedata` event
   (`sourceDataType: 'visibility'`). The old handler made layers visible inside
   that event, so the painter drew buckets whose `ProgramConfiguration._buffers`
   were never uploaded; this is the `VertexArrayObject.bind` `undefined.length` crash.
2. A source whose layers are all hidden is unused, drops its in-view tiles and
   reports `loaded() === true`. The gate therefore livelocked: show, tiles load
   (not loaded), hide (loaded), show again. A probe recorded this cycle every
   few seconds with 119,406-point tiles never finishing. The earlier rAF trial
   removed the crash but not the livelock; it passed once only by timing luck.

**Fix.** `frontend/src/sourceGate.ts` ties visibility to data identity instead
of tile loading. An identity change hides that source's layers synchronously
and calls `setData()` (a Promise in MapLibre 6). Layers return only after that
Promise resolves for the current identity *and* a full map frame has rendered
since hiding, so the hidden frame already dropped tiles built from older data.
All map mutations happen outside MapLibre's render pass (React layout effect,
`style.load` or a coalesced animation frame). Layers are created hidden, and
the "drawn" status (`renderedKey`) still waits for actual tile loading.

Browser evidence (ignored, `data/local-verification/phase3/`):
`filter-races-1793982632520114200.json` and `browser-1793982567920123000.json`
from the workflow run, plus the earlier standalone passes
`filter-races-1793981286982030300.json` and `browser-1793982130410767800.json`.
Failed intermediate runs from this session remain alongside them. Baseline
rendered-feature checks now see all four operators (2,648 rendered line
features); the old map was hidden 99–483 ms after a date change.

Test-harness fixes (not product changes): stop-name comparison normalizes HTML
whitespace (ATP `Via  Baldedda`); React-controlled search inputs are cleared via
the native setter plus an `input` event, because Selenium `clear()` emits none;
the warning-date check waits for its route catalog; `verify_local.ps1` judges
native steps by exit code, since PowerShell 5.1 promoted Vite's redirected
stderr bundle warning to a terminating error.

**Measured performance (after change, browser idle; `after-http.json`).** Five
sequential loopback samples, median (max): summary 443.70 ms (1,418.77);
active stops 4,291.38 ms (12,141.52); cached regional network 505.48 ms (713.69);
ASPO timetable 235.28 ms (270.29); ASPO board 180.48 ms (210.59). Compared with
the baseline medians (1,265.45 / 4,522.50 / 302.32 / 379.88 / 184.14 ms), the
cached network median is within its observed sample spread (252.67–713.69 ms);
no regression is claimed from five samples. Isolated cold startup: 5.92 s and
6.24 s in two workflow runs.
Browser readiness with warm caches: all-four overview 7.71 s and 8.35 s; cached
date change to new map 5.21 s (10.25 s in an earlier standalone run). **The
five-second target is not met.** Active stops (≈4–12 s) are the dominant cost.
An uncached Nov 7 regional build during a browser run exceeded the test's 120 s
wait while other requests ran concurrently; its cache file appeared about five
minutes after the services started. This was incidental, not a controlled
benchmark. Caches and archives were not deleted to repeat it.

**Integration recommendation.** Local correctness verification is complete and
green. The branch is reviewable, but merging remains the user's decision and
was not done. Remaining limitations are P2 latency (active stops, uncached
regional geometry) and the existing Starlette/httpx and bundle-size warnings.

## November 7 (overnight) — measured latency fixes

Three changes, each found by profiling and each verified output-identical:

1. **Failed optional import on every query.** DuckDB 1.5.6's Python client tries
   `import pandas` on each `execute`. Pandas is not installed, and Python does
   not cache failed imports, so every query rescanned `sys.path` under the
   global import lock. One active-stops request spent about 1.8 s in 534 such
   probes, and concurrent requests serialized on the lock (12–19 s outliers).
   `storage.connect` now records known-absent pandas as `sys.modules['pandas']
   = None` (`test_duckdb_import_overhead.py`; red before, green after).
2. **Pure-Python simplification dominated uncached geometry** (about 54 of 60 s).
   `simplify_line` was rewritten with flat coordinate lists and branched
   clamping. It returns identical points: the oracle test
   `test_phase3_simplify.py` compares it with the original at every display
   level, and all 17 existing on-disk network views were regenerated
   byte-identical. Coarser budget levels are memoized per immutable snapshot shape.
3. **Restarts discarded that work.** `shape_store.py` persists simplified levels
   per snapshot under `data/cache/shapes/`, validated by snapshot id, archive
   SHA-256, format and algorithm version; corrupt or stale files are ignored.
   Later builds write small delta files off-request and compaction happens on
   load, so no build rewrites the 34 MB ARST document
   (`test_phase3_shape_store.py`).

Measurements (curl/urllib; PowerShell `Invoke-WebRequest` adds seconds on
multi-MB bodies and was discarded):

| Measure | Before | After |
|---|---:|---:|
| Active stops, HTTP median (5 samples) | 4,291 ms | 420 ms |
| Summary median | 444 ms | 142 ms |
| Cached regional network median | 505 ms | 269 ms |
| ASPO stop board median | 180 ms | 56 ms |
| Uncached regional build, in-process | 102.8 s | 48.5 s, first ever; 0.9 s for later dates |
| First uncached date after restart, HTTP | 47.8 s | 3.26 s; later dates 0.64–1.05 s |
| Browser all-four readiness, warm | 7.71 s | 5.35 s |
| Cached date change to new map (browser) | 5.21 s | 2.69 s |
| Backend tests | 63.9 s | about 21 s |

Run 4 (`verify-local-browser-20261107-run4.log`, shape store active): 80
tests, startup 4.81 s, all-four smoke readiness 5.47 s, race baseline 3.34 s,
cached date change 1.55 s, no JavaScript errors.

Evidence: `after-http.json`, `after-import-fix-http.json` and the run3/run4
verification logs. The five-second browser target is now met for warm
all-four readiness and cached date changes in these runs. It is **not** met
for the very first regional build on an empty shape store (about a minute,
once). A single headless sample is not a robust percentile.

## November 7 (overnight) — timetable pagination

`GET /api/v1/route/{snapshot}/{route}/timetable` accepts `offset` (≥ 0) and
`direction` (`0`, `1`, `null`, `all`) and `variant`. Filters apply to the complete
instant-ordered list before paging. Responses add `matching_departures`,
`offset`, `limit`, `next_offset` and `filters`. `total_departures` and the
variant statistics remain whole-route. Frequency windows appear on the first
page only. The inspector fetches filtered pages from the server and appends
pages with **Load more departures**. This removes the earlier "filters apply to
the loaded subset" limitation. Tests: `test_phase3_pagination.py` (pages
partition the full ordering; filters apply before paging; API validation).
Browser: real routes peak at 175 departures on Nov 6 (below one 200-row page),
so the smoke test rewrites the request page size to 50 in the browser. Against
the real server, ARST MCA1 (175 departures) showed 100 after one Load more,
identical to API pages 0 and 50, and a direction-0 filter (87 matching)
matched the server-filtered page.

## November 7 (overnight) — cache bounds and bundle split

`network_cache.prune` keeps disposable response files within 500 files / 1 GiB
(environment-configurable), evicting least-recently-used first. Reads refresh
mtime, the just-written key is never evicted, unrelated files are ignored, and
abandoned `.part` temporaries older than an hour are removed
(`test_phase3_cache_lifecycle.py`). The current 44 files / 75 MB are well within
bounds, so nothing was evicted. Vite now emits separate `maplibre` (1,064 kB),
`react` (222 kB) and app (37 kB) chunks. The long-standing size warning is
resolved with an explicit 1,100 kB threshold sized to the MapLibre vendor chunk.

## November 7 (overnight) — nearby stop records across sources

`nearby.py` adds `GET /api/v1/stop/{snapshot}/{stop}/nearby?radius=200&limit=30`.
It finds stop records from every selected snapshot within a haversine radius,
using a SQL bounding-box prefilter, and sorts them by distance. The stop
inspector lists them, and clicking one opens its board. Every response and the
UI carry the note that this is straight-line proximity, not a verified
physical-stop match, walking route or transfer. Tests
(`test_phase3_nearby.py`) cover cross-source 0 m matches, the radius boundary,
limits, missing coordinates and API validation. The browser smoke checks the
searched Olbia stop's list against the API.

## November 7 (overnight) — independent review of the night's diff

A separate read-only reviewer examined `ea4440b..b5af288`. It confirmed the core
invariants: identical simplification, lock-guarded shape cache, validated store
files, exact filtered paging, and MapLibre's shared `setData` promise preventing
an early gate on A→B→A identity changes. It reported these edge cases, now fixed:

1. "Drawn" status could flip before tiles loaded. A just-shown source is still
   "unused" until the next `Style.update()`, so it reports loaded. `SourceGate`
   now records when layers were shown and `drawn()` requires a later render plus
   `isSourceLoaded`.
2. A worker `setData` failure still opened the gate. MapLibre resolves the flush
   promise and reports the failure as a map `error` with `sourceId`. The gate now
   marks that identity failed and keeps it hidden. The overlay says "Map could not
   draw the current data; previous data hidden". Source errors no longer trigger
   the online-to-plain basemap fallback.
3. A partially hydrated store (above the 8,192-shape process cache) could compact
   away unloaded shapes. Compaction now runs only after a complete load.
4. Non-finite coordinates made the JSON write raise outside its `try`, failing
   requests repeatedly. Serialization errors now skip storing.
5. Interrupted writers could leave `.part` files in `data/cache/shapes/`. They
   are removed after an hour.
6. A failed "Load more" or filtered page could not be retried; a Retry control
   now calls the request's retry.
7. The inspector remount key now includes the stop's snapshot, not just its id.

Deliberately unchanged: nearby stops consider only the selected snapshots, which
by design are the ones the UI can show; and `windows_truncated` on later pages,
which the UI does not read. New tests: partial hydrate never compacts;
unserializable levels and stale parts fail safe.

## November 7 (overnight) — routes serving a stop

`GET /api/v1/stop/{snapshot}/{stop}/routes?date=` returns each route whose trips
call at the stop, with distinct trip templates and how many run on the GTFS
service day. Frequency trips are counted as templates, not expanded. The stop
inspector lists them (most calls first). Clicking one opens the route when the
current mode, operator and operating-date filters can show it; otherwise it
explains why it is hidden. Tests check counts against direct SQL, out-of-calendar
dates, missing coordinates and 404. The browser smoke compares the Olbia stop's
list with the API and opens an active route from it.

---

## Historical record: paused checkpoint (November 6–7)

Update, November 7: the user subsequently authorized committing and pushing this
unfinished checkpoint. See `PHASE3_HANDOFF.md` for the current remaining-work
checklist. The paused review below records the state before that authorization;
its local-only/uncommitted wording is historical. Browser completion remains
unverified; publishing the checkpoint does not imply integration readiness.

Executed November 6–7, 2026. **Paused at the user's request near the usage limit.
Phase 3 is not complete and is not ready for integration.**

Branch: `codex/local-real-feed-atlas`; HEAD/base:
`32abd1509182e8b998f94494a6f9d5cc458a0372`.
Phase 3 changes remain uncommitted. No Phase 3 push, merge, PR, remote issue,
deployment, download, reimport or GitHub Actions run was performed.

## Confirmed defects and changes

- Held responses reproduced November 6 geometry/stops under a newly selected
  November 8 date. Exact request identities, bounded successful-response caches,
  cancellation/completion guards, immediate layer hiding and scoped selections
  now address this. Final browser confirmation remains pending.
- Coverage previously excluded no-trip records before reporting missing geometry.
  API/UI now expose explicit reconciled source/mode denominators and per-source
  categories. SQLite/DuckDB regressions pass, including route/point limits and
  partially rendered variants.
- Cached geometry waited behind unrelated uncached builds. Cache hits now bypass
  that lock. A gated concurrent-request regression failed before and passes after.
- Timetables sorted local clock text incorrectly across the autumn DST fold.
  Exact departures and frequency windows now sort by actual UTC instant.
- Stop-board lookback omitted the sum of frequency end and downstream template
  offset. The horizon now includes that sum. Tests cover long offsets, overnight
  dates, exceptions, agency/stop timezones and exact/non-exact semantics.
- No selected exact-frequency instance-offset defect was reproduced. Existing
  offset behavior was retained and tested; non-exact windows remain windows.

## Independent data verification

The new audit streams archived raw CSVs and queries DuckDB read-only, without
application schedule imports. **317 checks passed**, with no discrepancies.

| Source | Routes | Stops | Trip templates | Nov 6 scheduled | Nov 7 | Nov 8 | Nov 9 |
|---|---:|---:|---:|---:|---:|---:|---:|
| ARST | 322 | 3,450 | 5,565 | 4,731 | 4,496 | 1,001 | 4,706 |
| ASPO | 15 | 422 | 394 | 319 | 317 | 189 | 319 |
| ATP Sassari | 50 | 1,032 | 3,404 | 1,023 | 889 | 489 | 1,023 |
| Trenitalia Sardegna | 2 | 42 | 796 | 200 | 174 | 119 | 200 |

Totals: 389 routes, 4,946 stops, 10,159 trip templates, 248,585 stop-time rows,
1,483,104 shape points. Scoped uniqueness/references, raw/SQL counts, ZIP integrity,
SHA-256, provenance timestamps and prior reports reconcile. There are 39 no-trip
routes (35 ARST, four ATP), 350 trip-referenced routes and 349 active Nov 6 routes.

ATP provenance records approved HTTPS operator URL
`https://atpsassari.it/media/files/atpss-gtfs.zip`, with Oct 20–Dec 31 calendars.
This verifies recorded local provenance and bytes, not a new observation of the
original retrieval. No insecure redirect was followed.
Calendar coverage: ARST Jan 1–Dec 31 (declared feed start Nov 1; no end), ASPO
Oct 27–Dec 31, Trenitalia Jun 6–Dec 12. ATP supplies no feed_info. Trenitalia is
calendar-exceptions-only; ATP exceptions are essential despite zero weekday flags.
No real feed has frequency rows; synthetic fixtures cover frequency semantics.

ARST's 156 missing-shape trip warnings collapse to 15 unique dashed schematic
features on BCA/BMA/BSS1/MSS. They are stop links, not verified road/rail alignments.

## Coverage contract

For source/mode filters before date filtering:

`records = no_trips + inactive_excluded + eligible`

`eligible = rendered + insufficient_geometry + omitted_by_limits`

Eligible means active trip-referenced routes with active-only enabled, otherwise
all trip-referenced routes. Partial routes are a subset of rendered. Source totals
sum to regional totals. The legacy `missing_geometry_routes` now means no-trip
plus insufficient-geometry records; prefer explicit coverage fields.

Nov 6: 389 records, 350 with trips, 349 active/eligible/rendered, 39 no trips,
one inactive excluded, zero insufficient/omitted/partial. The response retains
2,596 features and 119,406 overview points. The new cache format is
`regional-coverage-v3`; old cache files remain preserved.

## Verification and browser blocker

```powershell
.\.venv\Scripts\python.exe -m pytest -q backend/tests
# 74 passed, one Starlette/httpx deprecation warning, 107.46 seconds
npm run build --prefix frontend
# TypeScript + Vite passed; latest checkpoint build 33.73 seconds
```

Vite's large MapLibre bundle warning remains. Independent read-only diff review
found no actionable defect, but browser execution exposed a rendering issue.
The stronger browser tests received current-key data with correct features/routes,
yet network source readiness stayed false and layers hidden. The line renderer
threw `Cannot read properties of undefined (reading 'length')` in
`VertexArrayObject.bind` because paint buffers were undefined.

The latest trial defers synchronous `sourcedata` visibility updates to a coalesced
animation frame. It builds, but browser confirmation is pending; its causal
explanation remains a hypothesis. Unchanged MapLibre paint/filter setters already
have equality guards, so no speculative fix was added there.

Browser scripts now inspect actual source/rendered features; cover all four
operators, dates/modes, variants, exact trips/stops, boards, warnings and offline
mode; inject held, failed and out-of-order responses; and preserve screenshots,
console errors and full stacks. **Final green browser runs have not passed.**
`verify_local.ps1 -Feeds` now audits raw ZIPs/read-only SQL instead of importing;
`-Browser` runs both suites. The revised full workflow still needs executing.

## Performance evidence

Phase 3 baseline: five sequential loopback HTTP samples, medians including
serialization: summary 1,265.45 ms; active stops 4,522.50 ms; cached regional
network 302.32 ms; ASPO timetable 379.88 ms; ASPO board 184.14 ms.

First new-format uncached regional geometry: **80,687.3 ms**. It briefly overlapped
a synthetic test; treat this as an observed sample, not a robust benchmark.
No controlled after-change comparison, final cold-start or browser transition
measurement is complete. The five-second browser target is **not claimed**.
Incremental changes reuse frontend responses and stop GeoJSON conversions and let
cached geometry bypass unrelated builds; no supported routes/variants were dropped.

Ignored evidence: `data/local-verification/phase3/` contains independent audit,
raw metadata, baseline HTTP, cold geometry, Python results, red/failed-green races,
renderer stack/screenshots and unique service logs. An initial profiler invocation
overwrote the fixed Phase 2 performance file; exact recorded values were restored
from prior output. `evidence-preservation-note.txt` documents this transparently.
New profiler runs accept explicit output paths.

## Resume and remaining work

1. Resolve/verify the MapLibre trial and run both browser suites.
2. Run `scripts/verify_local.ps1 -Browser` with services running. Audit with backend
   stopped when needed; DuckDB cannot share a separate read-only process lock.
3. Measure controlled after-change HTTP, startup, source updates and transitions.
4. Reconcile final documentation, review all changes and commit **locally**.
   Preserve/exclude unrelated empty `codex-phase3f-review.patch` and ignored data.

P1: rendering verification blocker. P2: slow uncached geometry and stops.
P3: pagination, external GTFS validator, optional fields, cache lifecycle and
broader regional validation. **Do not integrate this checkpoint yet.**

## Launch and stop

```powershell
cd C:\Users\User\Desktop\TransportAtlas
.\scripts\stop_local.ps1
npm run build --prefix frontend
.\scripts\start_local.ps1 -Production
# http://127.0.0.1:5173/?date=2026-11-06&basemap=plain&diagnostics=1
.\scripts\stop_local.ps1
```

Services bind to 127.0.0.1. Existing data remains in `data/transport_atlas.duckdb`,
`data/downloads/` and `data/raw/`. No provider data, database, cache, log, screenshot
or secret belongs in the commit. Stop services at the pause to release resources.
