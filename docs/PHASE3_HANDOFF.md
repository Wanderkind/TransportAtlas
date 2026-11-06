# TransportAtlas — Phase 3 handoff

## Current state — November 7, end of overnight session

Branch `codex/local-real-feed-atlas`, HEAD `baa3b77`, pushed with CI skipped.
Not merged; no PR, deployment or remote CI. Integration is the user's decision.
Latest complete local check (`verify-local-browser-20261107-run9.log`):
`verify_local.ps1 -Browser`: 91 Python tests, loopback startup, TypeScript/Vite
build, real-data smoke and filter-race suites, with no JavaScript errors.

Overnight commits, in order (details in `CORRECTIVE_REVIEW_20261106.md`):

| Commit | Change |
|---|---|
| `e19f7eb` | MapLibre rendering blocker fixed (`sourceGate.ts`; visibility by data identity) |
| `bd75922` | Latency: DuckDB failed-import probe, identical faster simplifier, persistent shape store |
| `99bfd67` | Timetable pagination with server-side direction/variant filters |
| `74675f0` | LRU-bounded network response cache; vendor chunk split |
| `b5af288` | Nearby stop records across sources (proximity only) |
| `7d94a1a` | Fixes from an independent review of the night's diff |
| `baa3b77` | Routes serving a stop, with calls on the service day |

Measured now: active stops ≈ 0.42 s (was 4.3 s); first uncached date after a
restart ≈ 3 s (was ≈ 48 s); warm all-four browser readiness 3–6 s; cached date
change ≈ 1.6–2.7 s. The first-ever regional build on an empty shape store still
takes about a minute, once.

Remaining (each needs a user decision or new data, so not done unattended):
- External GTFS validation: needs downloading the MobilityData validator (Java).
- Optional GTFS fields (wheelchair, platform codes, colours): need importer
  changes plus a re-import of the verified database.
- Historical snapshot selection: only one snapshot per source is imported.
- Broader regional/country validation: needs new feed downloads.
- Starlette/httpx test-client deprecation: needs a dependency change (`httpx2`).
- Physical-stop matching and connectivity beyond the new proximity and
  routes-at-stop views; viewport/vector-tile loading for national scale.

Resume: `npm run build --prefix frontend`, `.\scripts\start_local.ps1 -Production`,
`.\scripts\verify_local.ps1 -Browser`, `.\scripts\stop_local.ps1`. Evidence lives in
ignored `data/local-verification/phase3/`. Leave the empty
`codex-phase3f-review.patch` untouched.

## Original checkpoint (before resolution)

Checkpoint: November 7, 2026 (Asia/Seoul).
Branch: `codex/local-real-feed-atlas`.

The user has authorized committing and pushing this checkpoint to GitHub.
**Development remains incomplete. Do not merge into main or claim that the final
browser checks pass.** No GitHub Actions, deployment or additional imports are
needed to resume. Keep backend services on 127.0.0.1.

Checkpoint publication checks rerun November 7: **74 Python tests passed in
81.15 seconds**, TypeScript/Vite production build passed (Vite 59.42 seconds),
changed Python scripts compiled, and `git diff --cached --check` passed. Existing
Starlette/httpx and bundle-size warnings remain. Browser checks were not rerun;
the rendering trial remains unverified.

## Start here: rendering blocker (P1)

The new real-data browser check received current-key regional geometry but
MapLibre did not finish loading its network source. The renderer threw
`Cannot read properties of undefined (reading 'length')` in
`VertexArrayObject.bind` (undefined paint buffers while drawing lines).

The latest trial in `frontend/src/main.tsx` defers source-event visibility updates
to a coalesced animation frame. It compiles, but its browser outcome is unknown.
Reproduce and diagnose before adding another workaround. The exact request hook,
immediate layer hiding and inspector scoping must continue preventing stale data
under newly selected dates/operators/modes.

Local evidence (ignored by Git):

- `data/local-verification/phase3/filter-races-red.json`: original stale-map race.
- `data/local-verification/phase3/filter-races.json`: failed green attempt.
- `data/local-verification/phase3/renderer-error.json`: full rendering stack.
- Unique screenshots and any interrupted retry artifacts in that directory.

Launch the current build and run the condition-driven checks:

```powershell
cd C:\Users\User\Desktop\TransportAtlas
npm run build --prefix frontend
.\scripts\start_local.ps1 -Production
# http://127.0.0.1:5173/?date=2026-11-06&basemap=plain&diagnostics=1
.\.venv\Scripts\python.exe scripts/smoke_filter_races.py
.\.venv\Scripts\python.exe scripts/smoke_real_ui.py
.\scripts\stop_local.ps1
```

Tests need local Chrome and the existing Selenium environment. They inspect
actual MapLibre sources/rendered features, not just React elements. Preserve
failed artifacts. Verify all four operators, exact trip stops/times/geometries,
direction/variant controls, stop boards, date/mode/operator toggles, offline mode,
truncation warnings, loading/errors and delayed/out-of-order completions.

## Complete local verification

- [x] Resolve rendering failure and obtain passing runs of both browser scripts.
- [x] Run the complete workflow with services running:
  `.\scripts\verify_local.ps1 -Browser`.
- [x] (Not needed: data-audit code unchanged.) If data-audit code changes, stop services and run
  `.\.venv\Scripts\python.exe scripts/audit_local_evidence.py --output data/local-verification/phase3/resume-audit.json`.
  DuckDB requires stopping the backend before a separate audit process opens it.
- [x] Review the complete diff and reconcile the status/report with observed
  results. Browser checks pass; integration remains the user's decision.

The previous checkpoint recorded 74 passing Python tests, a successful
TypeScript/Vite build and 317 passing independent feed-audit checks. See
`CORRECTIVE_REVIEW_20261106.md` for details and evidence boundaries. These do not
establish browser rendering correctness. Starlette/httpx deprecation and the
large Vite MapLibre bundle warning remain visible.

## Measure remaining performance (P2)

- [x] Measure isolated cold backend startup with `scripts/check_startup.py` (5.92 s, 6.24 s).
- [x] Run controlled after-change HTTP samples while browser tests are idle:
  `.\.venv\Scripts\python.exe scripts/profile_local.py --output data/local-verification/phase3/after-http.json`.
- [x] Record frontend data arrival, MapLibre source readiness, rendered readiness
  and date/filter transitions using `?diagnostics=1` (browser reports' timings).
- [x] Compare with the preserved baseline; do not claim the five-second target
  without a direct measurement. Keep all supported routes and variants.

Baseline medians: summary 1,265.45 ms; stops 4,522.50 ms; cached network 302.32 ms;
ASPO timetable 379.88 ms; ASPO board 184.14 ms. A first uncached new-format
regional build took 80,687.3 ms. No controlled final after-change comparison exists.
Do not delete archives or old caches to repeat that expensive measurement.

## Preserve the verified data

All four official feeds are already imported. Do not download or reimport them
merely to resume UI work. Independent raw ZIP/SQL checks verified:

| Source | Routes | Stops | Trip templates | Nov 6 scheduled trips |
|---|---:|---:|---:|---:|
| ARST | 322 | 3,450 | 5,565 | 4,731 |
| ASPO | 15 | 422 | 394 | 319 |
| ATP Sassari | 50 | 1,032 | 3,404 | 1,023 |
| Trenitalia Sardegna | 2 | 42 | 796 | 200 |

There are 389 route records, 350 with trips, 349 active Nov 6 and 39 without trips.
The latter are source-data gaps, not proof of absent service. Coverage must retain:
`records = no_trips + inactive_excluded + eligible` and
`eligible = rendered + insufficient_geometry + omitted_by_limits`.
Partially rendered routes are a subset of rendered routes.

`data/transport_atlas.duckdb`, `data/downloads/`, `data/raw/`, `data/cache/` and
`data/local-verification/` stay local and ignored. Preserve the unrelated empty
`codex-phase3f-review.patch`. Do not commit data, screenshots, logs or secrets.
ATP's local recorded HTTPS provenance is verified, but the audit did not
re-observe its original network retrieval. Keep that distinction in reports.

## Later milestones (P3)

~~Timetable pagination~~ (done Nov 7: `offset`, server-side direction/variant
filters, Load more); external GTFS validation; more optional GTFS fields;
~~cache lifecycle~~ (network responses LRU-bounded Nov 7; shape store compacts deltas); historical snapshot selection; broader regional/country
validation; physical-stop matching (a first step landed Nov 7: cross-source
nearby stop records, explicitly proximity only) and connectivity/accessibility
analysis (first step Nov 7: routes serving a stop, with calls on the service day).
Prioritize the rendering blocker and measured latency before these features.

## Documentation to finalize

- `docs/CORRECTIVE_REVIEW_20261106.md`: final green evidence, measurements and
  integration recommendation. Its date is the requested filename, not a claim
  that all work ran on November 6.
- `docs/IMPLEMENTATION_STATUS.md` and `docs/LOCAL_DEVELOPMENT_PLAN.md`: replace
  paused/uncommitted status once the remaining work actually completes.
- `README.md`: keep launch, stop and verification commands consistent.

The earlier Phase 3 local-only constraint was superseded for this checkpoint by
the user's explicit push request. Future development should continue locally;
do not infer authorization to merge, deploy, open a PR or trigger remote CI.
