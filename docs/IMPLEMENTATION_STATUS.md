# Implementation status — Windows local development, 2026-11-06

Development branch: `codex/local-real-feed-atlas`, based on main
`8c065b390f97667d63d3300d1eab171ad0aadd6e`. All imports, builds, API checks and
browser tests run locally. No GitHub Actions or public deployment are needed.
The user subsequently authorized committing the finished work to GitHub; publish
only this development branch, with CI skipped, without merging main or opening a PR.

## Real feeds verified locally

All four sources coexist in `data/transport_atlas.duckdb`. ZIP CRC, SHA-256 and
raw/imported row counts were checked for every normalized GTFS table. Archives,
retrieval timestamps and source URLs are retained locally. Counts are source
records, not deduplicated geographic totals.

| Source | Route records | Stop records | Trip templates | Nov 6 scheduled trips | Nov 6 active routes | Import seconds |
|---|---:|---:|---:|---:|---:|---:|
| ASPO Olbia | 15 | 422 | 394 | 319 | 15 | 119.231 |
| Trenitalia Sardegna | 2 | 42 | 796 | 200 | 2 | 91.046 |
| ARST | 322 | 3,450 | 5,565 | 4,731 | 286 | 193.401 |
| ATP Sassari | 50 | 1,032 | 3,404 | 1,023 | 46 | 13.942 |

Totals: **389 imported routes, 4,946 stop records, 349 operating routes and 6,273
scheduled trips on November 6**. None of these four snapshots has frequency rows;
exact/non-exact frequency semantics are covered by synthetic regression fixtures.

**39 imported route records have no trip references at all**: 35 ARST and 4 ATP.
The route list retains them, but their geometry cannot be inferred from these
feeds. With the operating-only filter disabled, all **350 trip-referenced routes**
map without truncation: 3,388 alignments, 81,709 overview points and a 0.0033-degree
bound. On November 6, 349 of them are active. This source-data gap is not proof of
absent real-world service. `route-coverage.json` records raw ZIP reconciliation
and unused route IDs; `network-all-imported.json` preserves the checked response.

| Source | Nov 7 Saturday | Nov 8 Sunday | Nov 9 Monday | Observed calendar coverage | Declared GTFS feed_info validity |
|---|---:|---:|---:|---|---|
| ASPO | 317 | 189 | 319 | 2026-10-27–2026-12-31 | Same range |
| Trenitalia | 174 | 119 | 200 | 2026-06-06–2026-12-12 | Same range |
| ARST | 4,496 | 1,001 | 4,706 | 2026-01-01–2026-12-31 | From 2026-11-01; end unspecified |
| ATP Sassari | 889 | 489 | 1,023 | 2026-10-20–2026-12-31 | Not supplied |

Publisher-declared validity is separate from observed calendar bounds. Summary
coverage checks respect supplied declared bounds, while actual service counts
still follow calendars and exceptions. Trenitalia is calendar-dates-only; ATP's
exceptions are essential to its active services. Missing/out-of-coverage records
are not evidence that transport does not exist.

ATP now has a directly discovered approved HTTPS operator resource:
`https://atpsassari.it/media/files/atpss-gtfs.zip`. Its raw calendars cover the
research date, consistent with the current official Sardegna Mobilità catalog.
The historical unapproved HTTP Google redirect was not followed or weakened.

SHA-256 of archived bytes:

```text
aspo                003278f32ac43d90a69bc4e603f124d261b80345c300fefa658ad8f0195cb382
trenitalia_sardegna  b27657fb8ff6921481e3d903dbe0217fe106e149a732ccff6c0857b78c1aa0cc
arst                2a10d17cca9cbace8205e00740b3c55300ea0fdf7245ab55a4a61bb34ae1bde8
atp_sassari         5398fc76c8ce5ae0cea85e8d2e3f2f72ea88c050bcb5720469e5280c18dbe3e5
```

Evidence: `data/local-verification/feeds.json` contains per-table raw/database
counts, URLs, UTC retrieval times, date summaries, warnings and timings.
`field-audit.json` lists archived optional fields not normalized into SQL and
declared `feed_info`. These include accessibility/bicycle flags, distances along
shapes, descriptive fields, stop headsigns, continuous pickup/dropoff and transfer
metadata. They remain recoverable in the original ZIPs, not silently invented.
ARST has **156 missing-shape trip-template warnings**; the other imports report
none from the current custom importer. No external MobilityData validator ran.

## Geographic and timetable behavior

- Four-source simultaneous map, independent operator toggles and single-source
  selection; date and bus/rail/tram/ferry filters; source-scoped route/stop search.
- Route selection dims unrelated lines. Direction highlights and variant selection
  preserve distinct stop sequences and shape IDs. Clickable departures load full
  ordered stop lists and original trip geometry; stops expose departure boards.
- Clustered stop layers, fit controls, online street context and a plain mode that
  makes no external tile requests. The MapLibre worker is bundled locally.
- Calendar rules/exceptions, full-route first/last departures, daily counts,
  per-variant median origin intervals, and source/hash/validity evidence.
- GTFS times above 24:00 and previous-service-day overnight boards are retained.
  Frequency-only windows never produce fabricated exact departures; template
  times are labeled until an exact operating instance is selected.
- Every distinct schematic stop sequence remains dashed and identified as
  schematic. It is not a claimed road or railway alignment.

The November 6 active network contains **2,596 alignments and 119,406 displayed
points across all 349 active routes**, with **no truncation and zero missing
route geometries**. Raw active source shapes contained 1,342,922 points. Adaptive
overview simplification uses a conservative 0.0017-degree bound for this view;
selected trips retain their original alignment. Limits are applied across the
whole selected network, with explicit warnings when they cannot be satisfied.

## Local environment and performance

Python 3.12 virtual environment, DuckDB 1.5.6, FastAPI 0.162.0, Node 24.19.0,
npm 11.17.0, TypeScript 5.9.3, Vite 7.3.7, React 19.3.1 and MapLibre 6.16.0.
The system default Python is 3.10.8 and must not be used for this project.
`tzdata` supplies Windows timezone data. Native Windows certificate validation
solved ASPO's missing-intermediate chain without weakening HTTPS or host checks.

Imports use transactional DuckDB COPY for larger batches, preserving null/empty
values, quoted text, Unicode and rollback. The first uncommitted row-at-a-time
ASPO attempt was stopped; completed imports were not repeated just to benchmark
later tuning. Recorded import durations above are the original successful runs.

Initial geographic profiling took 40–90 seconds and exhausted the point budget
before later operators appeared. Adaptive allocation fixed missing routes;
serialized snapshot/filter caches removed recursive response encoding overhead.
Five sequential loopback samples after caching recorded these medians:

| Endpoint | Median milliseconds |
|---|---:|
| Summary | 694 |
| All active stop markers, 1.0 MB | 2,651 |
| Complete active network, 3.4 MB | 170 |
| ASPO route timetable | 279 |
| ASPO stop departures | 145 |

These are small local samples, not statistically robust p95 guarantees. The first
uncached adaptive regional build still took about 76 seconds. Disk caches survive
restarts; unseen dates/filters can require slower first builds. A production-browser
check subsequently exposed a slow global stop query and duplicate initial stop
requests; those were replaced with direct joins and a single scoped initial load.
Final verification and updated timing evidence are in `verification-final.log`,
`browser.json` and `performance.json` under `data/local-verification/`.

The final production-preview browser reached its initial data/canvas state in
**8.04 seconds**. This improves the earlier 26.68-second preview and 66.88-second
development-server checks, but still misses the specification's 5-second target.
Global stop loading and uncached geometry/date transitions remain performance
work; these measurements are not a GPU frame-time benchmark.

## Verification and commands

```powershell
cd C:\Users\User\Desktop\TransportAtlas
.\scripts\verify_local.ps1 -Browser
.\.venv\Scripts\python.exe scripts/profile_local.py
npm audit --omit=dev --prefix frontend
```

The workflow runs the full Python/DuckDB/API suite, actual loopback Uvicorn startup
with a temporary database, TypeScript/Vite production build and Chrome smoke against
the real sources. The browser checks route/stop inspectors, ordered stops, provenance,
operator controls, mode filters, Sunday service, fit and basemap switching. Screenshots
are `dashboard.png` and `dashboard-interactions.png` in the local evidence folder.

Final workflow result: **42 Python tests passed**, backend startup passed in
**3.016 seconds**, TypeScript/Vite built in **11.82 seconds**, and the real-data
Chrome smoke passed. The final simplification regression proves that an unchanged
point count at one tolerance step does not prematurely drop routes.

## Files changed

- Backend: `api.py`, `ingest.py`, `network.py`, `official.py`, `schedule.py`,
  `storage.py`, `timetable.py`; new `network_cache.py`, `provenance.py` and
  `route_details.py`; tests in `test_network.py` and new `test_local_atlas.py`.
- Frontend: `package.json`, new `package-lock.json`, `vite.config.ts`, `src/api.ts`,
  `src/main.tsx`, `src/styles.css`; new `src/Inspector.tsx`, `src/icons.tsx` and
  `src/vite-env.d.ts`.
- Local tools: `start_backend.ps1`; new `start_local.ps1`, `stop_local.ps1`,
  `verify_local.ps1`, `check_startup.py`, `import_local_feeds.py`,
  `profile_local.py` and `smoke_real_ui.py`.
- Setup/documentation: `.gitignore`, `pyproject.toml`, `README.md`,
  `docs/IMPLEMENTATION_STATUS.md`, `docs/REAL_FEED_VALIDATION.md`,
  `docs/V02_NETWORK_ATLAS.md` and new `docs/LOCAL_DEVELOPMENT_PLAN.md`.

The unrelated empty local `codex-phase3f-review.patch` is preserved and excluded
from the development commit. No provider ZIP, database, screenshot, log, generated
cache, environment or secret is included.

The dependency tree is valid and the production npm audit reports zero vulnerabilities.
An interrupted npm install initially left an incomplete caniuse-lite directory and
an invalid generated lock entry; both were repaired with local backups retained.
MapLibre was upgraded to the patched major and its Vite worker configured explicitly.
Vite still reports the large map bundle, and Starlette emits an httpx deprecation
warning in tests. Neither warning is suppressed.

Start the built dashboard, or stop/restart if services are already recorded:

```powershell
.\scripts\stop_local.ps1
npm run build --prefix frontend
.\scripts\start_local.ps1 -Production
# http://127.0.0.1:5173/?date=2026-11-06
# Offline: add &basemap=plain
.\scripts\stop_local.ps1
```

Use `start_local.ps1` without `-Production` for Vite live development. Both services
remain bound to loopback. The stop script checks owned process identities and stops
their child processes. ZIPs, databases, logs, screenshots, caches and environments
remain ignored. The README documents fresh installation and imports.

## Remaining work and next milestones

1. Persist reusable multiresolution shape data or introduce viewport/vector-tile
   loading to improve uncached regional/date transitions. Cache files can accumulate
   across explored dates; no national-scale performance claim is made.
2. Add timetable pagination. The UI currently loads 200 departures/windows, clearly
   discloses truncation and distinguishes full-route statistics from subset filters.
3. Run the external GTFS validator, normalize more optional fields and complete
   licensing/attribution metadata. Current warnings are custom-importer checks.
4. Add historical snapshot-selection controls, exports, physical-stop matching and
   connectivity/accessibility analysis, then validate a second country/region.

Suitable for local research exploration. Provider schedule accuracy, route coverage
completeness, live operations and feasible passenger transfers are not guaranteed.
