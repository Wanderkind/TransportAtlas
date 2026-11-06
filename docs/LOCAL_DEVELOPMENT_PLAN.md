# Local development plan — 2026-11-06

The assignment in this chat is the specification. Work in the existing checkout,
on `codex/local-real-feed-atlas`, initially without remote writes or Actions. The
user subsequently authorized committing the finished work to GitHub: push only
the development branch with CI skipped, without a PR or main merge. Preserve local
data and HTTPS/source restrictions. The user explicitly requested autonomous
execution; no additional design approval or worktree is needed.

## Approach

Extend the existing Python/FastAPI/DuckDB and React/MapLibre application. Reuse
official discovery, immutable archives, calendar calculations and read-only APIs.
Avoid a replacement UI, database migration or speculative tiling system until
real-feed measurements justify it.

## Tasks and verification

- [x] Audit tools and initialize Python 3.12 environment; install absent dependencies.
  Run `python -m pytest -q backend/tests` and `npm run build` as baseline.
- [x] Add a resumable local official-feed verification command using one DuckDB,
  validated provenance sidecars and raw count reconciliation; run ASPO, Trenitalia,
  ARST, then ATP. Record weekday/weekend summaries, warnings and elapsed times.
- [x] Add regression tests for tram separation, scoped stop filters, route detail
  statistics/variant metadata and calendar provenance; observe failure then implement.
- [x] Improve the existing UI with independent source toggles, stop search,
  scoped markers, direction/variant geometry, full trip stop inspection and feed
  provenance. Verify TypeScript/Vite and a local browser with real data.
- [x] Add a PowerShell verification entrypoint and update local instructions/status.
  Run full local tests, startup/API/browser checks and review the final diff.

## Review focus

Calendar exceptions and overnight times must retain GTFS semantics. Frequency-only
services must not produce precise departures. Missing or stale data is uncertainty.
Capped network, stop and timetable results must visibly disclose limits. Multiple
feeds must retain snapshot-scoped identities. Raw ZIPs, DBs, logs and reports stay
under ignored data paths. No import should repeat needlessly.

## Execution record

- Clean main at `8c065b390f97667d63d3300d1eab171ad0aadd6e`.
- Python default 3.10.8; installed 3.12 selected explicitly. Node 24.19.0, npm 11.17.0.
- No existing environment, node_modules, downloaded feeds or DB under repository data.
- All four official sources imported and reconciled, including a newly working
  approved HTTPS ATP resource. Archives/provenance and local evidence remain ignored.
- Local baseline: 29 Python tests passed; final full run: 42 passing.
- Fresh code review findings fixed with regressions: scoped search before caps,
  historical provenance, and simplification continuing past point-count plateaus.
- Rendering changed from 280 missing active routes to all 349 active routes present.
- Final workflow passed: Python tests, loopback startup, TypeScript/Vite build and
  real-data browser smoke. Production preview initial readiness: 8.04 seconds;
  the 5-second target remains unmet. Uncached geometry remains slower.
- All 350 trip-referenced routes map without truncation when inactive routes are
  included. Another 39 raw route records have no trips; this was reconciled against
  the original ZIPs, preserved, and documented instead of inventing alignments.
- Publish the verified development commit with CI skipped; preserve the checkout,
  downloaded feeds, database, generated evidence and unrelated empty review patch.
