# Phase 3 corrective review — paused checkpoint

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
