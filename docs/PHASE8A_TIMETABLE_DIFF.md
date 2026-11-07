# Phase 8A — Per-trip historical timetable differences

**Date:** 2026-11-07. Local commit only. This phase is read-only on all real data.

**Why it was needed.** History's comparison summary counts operating trips and static
route/stop/shape records. For ASPO on 2026-11-06 it reports `319 → 319` trips. That hides the two
timetable revisions Phase 7D0 found by independent analysis. Phase 8A makes them visible without
mistaking trip-ID renumbering or shape-ID changes for changed departures.

## Semantics

- **Same date only.** Per-trip differences are defined only for the `same_date` policy: one GTFS
  service day evaluated in both versions.
  - `different_dates` returns `status: "incomparable"` with the reason.
  - A date outside either version's calendar coverage also returns `incomparable`.
  - More than 30,000 operating trips on one side returns `unsupported`.
- **Operating trips** come from `schedule.active_services`, the same rule the rest of History uses:
  calendar weekday and date range first, then `calendar_dates` additions and removals take
  precedence.
- **What a timetable is.** The ordered stop IDs, plus each stop's arrival and departure in GTFS
  service-day seconds. Times past 24:00 are shown faithfully, e.g. `25:10:00`.
  - Route, direction and headsign changes are reported as changes.
  - **`shape_id` is never part of the timetable.** A trip whose only difference is its shape ID is
    `unchanged` and flagged `shape_id_changed`.
- **Frequency-based trips** (`frequencies.txt`) have no single exact timetable. They are
  `unsupported`, never given invented exact departures.
- **What a difference means.** It is a change in the **published records**. It is not proof of a
  change on the road, nor of when the publisher made it. Stop IDs are GTFS records of one source,
  not physical stop identities.

## Matching (transparent, deterministic, never forced)

| stage | rule | `confidence` |
|---|---|---|
| `same_trip_id` | Same trip ID in both snapshots of the source | `exact_id` |
| `identical_timetable_new_id` | Different IDs; same route, direction, stops and times, and that sequence occurs **exactly once** on each side | `identical_timetable` |
| `unique_similar_trip` | Different IDs; same route and direction, first departure within 30 min, stop-sequence similarity ≥ 0.6 (difflib ratio). Each trip must be the other's **single best** candidate, ranked by smallest departure shift and then highest similarity, with **no tie** | `constrained_unique` |

Every match reports its basis, including the departure shift in seconds and the similarity.

Trips that are not matched:
- **`ambiguous`:** it had several equally good free candidates (listed in `candidates`).
- **`removed` / `added`:** no safe match, or its closest candidate went to a strictly better pair
  (the note says which). These are record changes, possibly a renumbering or seasonal revision, and
  never evidence of a real-world closure.

Every response includes a **reconciliation** check: each operating trip on each side is accounted
for exactly once.

## API (additive; `/compare` is unchanged)

- `GET /api/v1/history/{source}/timetable-diff?baseline=&comparison=&policy=same_date&date=YYYY-MM-DD`
  - Optional: `&status=differences|all|changed|added|removed|ambiguous|unsupported|unchanged`, `&route=`,
    `&limit=1..500`, `&offset=`.
  - Returns:
    - `status` and `reason`, the `note` and `matching` texts;
    - both sides with their provenance;
    - archive attribution notices (shared with `/compare`), the synthetic flag;
    - `counts`, `matched_by`, `reconciliation`, `shape_id_only_changes`;
    - `routes`, the records page and its `page` metadata.
  - Records are ordered by route, then departure, then trip ID.
  - Results are cached per pair and date in the existing source-scoped History cache.
- `GET /api/v1/history/{source}/timetable-trip?baseline=&comparison=&baseline_trip=&comparison_trip=`
  - Stop-by-stop lists for one pair (or for one side of an added or removed trip), with names and the
    aligned differences: time changes, added stops and removed stops.
  - Reads only those trips by primary key.

Reads are scoped to one snapshot each. Operating trips and their `stop_times` are fetched with
`snapshot_id = ?` plus the day's service IDs, using the `(snapshot_id, trip_id, stop_sequence)`
primary key. No shapes are loaded. The selected atlas snapshot is never touched.

## UI

The History comparison now has a **"Timetable changes on selected service date"** section, between
the summary and the route/stop record tables.
- **Counts line:** for example, "319 → 319 trips operate on 2026-11-06: 317 unchanged, **2 changed**…".
  It adds the number of unchanged trips whose only change was the shape ID, and says that equal trip
  totals do not mean every trip is unchanged.
- **Filters:** status and route, with paging ("Load more trips").
- **Rows:** route, status, old and new trip IDs, departure, last arrival and stop count (old → new),
  a change summary and the match basis.
- **Stop detail:** the "Stops" button expands a row to the stop-by-stop differences, loaded on
  demand.

It follows the existing `useKeyedRequest` and paging patterns, with loading, error and retry states,
and no stale results when the source or date changes. Test hooks include `data-timetable-status`,
`data-counts`, `data-trip-status`, `data-trip-record` and `data-stop-change`.

## Real ASPO demonstration (read-only)

Baseline `396fbbac…` (Transitland archived copy, observed 2026-08-28), comparison `2fdf3861…`
(current), same date **Friday 2026-11-06**. This was run first through `SnapshotHistory` on a
read-only DuckDB connection, then in the browser (§Verification).

| | |
|---|---|
| operating trips | 319 → 319, reconciled 319/319 on both sides |
| matched by | 317 `same_trip_id`, 2 `unique_similar_trip` |
| unchanged | **317**, of which **295** differ only in `shape_id` (shape renumbering, correctly not counted) |
| changed | **2** |
| added / removed / ambiguous / unsupported | 0 / 0 / 0 / 0 |

Changed trips:

| route | baseline → comparison trip | match basis | change |
|---|---|---|---|
| 06 | `217185` → `217496` | unique similar trip, direction 0, departure shift 0 s, similarity 0.959 | 72 → 76 stops; last arrival 07:20:55 → 07:23:10 (departs 06:25:00 in both) |
| 11 | `217216` → `217497` | unique similar trip, direction 0, shift +300 s, similarity 1.0 | departs 13:00:00 → 13:05:00 (+5 min); last arrival 13:34:56 → 13:39:56; every stop shifted 5 min |

This independently reproduces the Phase 7D0 analysis. There was no false mass change from shape
renumbering, and no ambiguous cases. Real database SHA-256 `1deb031a…`, unchanged before and after.

## Tests

`backend/tests/test_phase8a_timetable_diff.py`: 14 tests, on SQLite and disposable DuckDB, using
invented feeds (`backend/tests/timetable_fixtures.py`). They cover:
- **Matching and classification:**
  - identical timetable with only a different `shape_id`;
  - same ID with an intermediate time changed;
  - a renumbered ID with an identical timetable;
  - a uniquely matchable 5-minute shift under a new ID;
  - an added stop;
  - ambiguous candidates (a tie, never forced), and identical twins as ambiguous;
  - a claimed candidate shown as removed with a reason, not "ambiguous";
  - direction, time-window and stop-similarity limits;
  - genuinely added and removed trips.
- **Service-day rules:**
  - an overnight trip past 24:00 (`25:10:00` → `25:15:00`);
  - `calendar_dates` removal and addition;
  - a frequency-only trip as unsupported;
  - a date with no service;
  - a date outside coverage;
  - the `different_dates` policy as incomparable;
  - same-snapshot comparison.
- **API and safety:**
  - comparisons across sources refused;
  - filters, pagination and a stable order;
  - the trip cap reported as unsupported;
  - stop-by-stop detail;
  - the API is additive (`/compare` is unchanged) and the selection is unchanged.

## Verification

- **Python, services stopped:** **313 passed, 0 skipped**. Real database `1deb031a…` before and after.
- **TypeScript and production build:** passed.
- **Synthetic History browser smoke:** passed for the `legacy`, `browser-archived`, `archived` and
  `activated` demo stages. The timetable section reconciled, and a changed trip expanded to its
  stop detail.
- **`verify_local.ps1 -Browser` on the real database (port 8765):** passed. Log:
  `data/local-verification/phase7d2/verify-phase7d2-20261107-224825.log`.
  - **pytest:** 307 passed, 6 skipped. The skips are the real-database read-only tests, which cannot
    open the file while the backend holds DuckDB's lock; all 313 passed with services stopped.
  - **Startup:** 12.8 s.
  - **Browser checks:** the four-source smoke, filter races and data-quality checks all passed.
  - **Real History check:** passed. In the browser, the ASPO same-date comparison of `396fbbac` against
    `2fdf3861` shows the archive notice, no synthetic banner, and timetable counts
    `{changed: 2, unchanged: 317, added/removed/ambiguous/unsupported: 0}`. The changed trips are
    `217185→217496` and `217216→217497`, and a changed trip expands to its stop detail.
  - **Afterwards:** the selection is unchanged. With services stopped, the database is still
    `1deb031a…` with modification time 2026-11-07T12:20:53Z.

## Limitations and Phase 8B candidates

- **Frequency-based trips** are listed as unsupported, not compared window by window.
- **Matching** is conservative by design. A renumbered trip that also moved more than 30 min, changed
  direction, or kept less than 60 % of its stop sequence shows as removed + added.
- **Different-dates policy:** not supported for per-trip differences.
- **Physical identity:** stops are matched by ID only. A renamed or moved stop with the same ID shows
  as unchanged in the timetable view; the stop-record table already reports field changes.
