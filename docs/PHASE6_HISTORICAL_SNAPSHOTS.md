# Phase 6: Historical snapshot inventory and comparison

Baseline: `codex/local-real-feed-atlas` at `589b0a7` (Phase 5, accepted). The
checkout matched it at the start; the untracked `PHASE4_APPLY.md` and
`codex-phase3f-review.patch` were left untouched. Baseline checks: **120 Python
tests passed** and the frontend production build passed.

> **Real longitudinal comparison is not yet possible.** The user's database
> holds exactly one imported snapshot for each of ARST, ASPO, ATP Sassari and
> Trenitalia Sardegna. Everything below was built and verified with **synthetic**
> two-version feeds of a fictional operator. Synthetic results are never
> observed history of Sardinia, and the UI labels them as synthetic.

## How the existing model works (audit)

| Piece | Behaviour | Consequence for history |
|---|---|---|
| `feed_snapshots` | One immutable row per `(source_id, content_sha256)` (unique index). `snapshot_id` = hash of source + archive SHA-256. Holds `retrieved_at`, `raw_zip_path`, `import_status`, observed calendar bounds `feed_start`/`feed_end`. | Older versions stay queryable after newer imports. Duplicates are refused as `unchanged`. |
| Archived ZIPs | `data/raw/<source>/<sha256>.zip` (+ provenance sidecar), verified on import. | Each snapshot can show its archive and `feed_info`, and can be re-hashed on demand. |
| `selection_members` | `source_id` primary key: exactly one current snapshot per source. `import_file` points it at each new import. | The atlas shows one version per source. |
| Current API | Every atlas endpoint goes through `schedule.active_snapshots()` (ready **and** selected). | It must keep doing so; history must not call it. |
| Network cache | Key includes the selection tuple, date and filters. | It is never used for history. |
| Quality caches | Keyed by `snapshot_id` (immutable). | Safe for any snapshot. |
| Service calendars | `active_services(con, snapshot_id, day)` applies `calendar` and `calendar_dates` per snapshot. | Reused unchanged for historical service days. |

Functions that assume one active snapshot per source: `active_snapshots` and
everything built on it (`/snapshots`, `/summary`, `/routes`, `/stops`,
`/network/geometry`, the nearby search scope). None of them were changed.

## Design decisions

- **Additive and read-only.** The new module `backend/transport_atlas/history.py`
  (`SnapshotHistory`) takes explicit snapshot IDs, never calls
  `active_snapshots()`, and only *reads* `selection_members`, to show which
  snapshot is current. There are no schema changes and no copied rows, and the
  real database opens read-only in tests.
- **Per-app caches keyed by immutable IDs.**
  - Per-snapshot facts (routes, stops, trips, frequencies, and the grouped
    stop→service map) are cached by `snapshot_id`.
  - Shape point digests are computed lazily, only when comparing.
  - Comparisons are cached by `(baseline_id, comparison_id, baseline_date,
    comparison_date)`, at most 64 entries. Paging reuses the cached comparison.
  - None of these caches is shared with the atlas network or quality caches.
- **Three things are never conflated:**
  - the retrieval time (`retrieved_at`, the local download);
  - the publisher's version label and declared validity (`feed_info`; GTFS has
    no publication date, so none is shown);
  - the GTFS service date.

## API (all GET, read-only)

### `/api/v1/history/sources`
`{sources: [{source_id, publisher, snapshots, ready_snapshots, selected_snapshot_id, comparison_possible}]}`

### `/api/v1/history/{source_id}/snapshots?date=YYYY-MM-DD&verify_archives=false`
Every snapshot of the source, of any status, ordered by retrieval:
- `snapshot_id`, `content_sha256`, `import_status`, `comparable` (ready only),
  and `selected` (the current atlas choice);
- `retrieved_at` with a `retrieval_note`;
- `archive {path, present, bytes, sha256_verified}`. The hash is checked only with
  `verify_archives=true`, and the result is cached by path, size and mtime;
- `feed_info {present, publisher_name, feed_version, version_label_date,
  declared_start, declared_end}`;
- `observed_coverage {start, end}`, and `date_in_coverage` when a `date` is given;
- `counts {route_records, routes_with_trip_references, stop_records,
  stops_referenced_by_trips, trip_templates, shape_ids}`;
- `validation`, filled only if a local MobilityData report matches this exact
  archive hash; otherwise `null` with "No validation report matches this archive
  hash."

Also returned: `ready_snapshots`, `comparison_possible`, and a `note`. With fewer
than two ready snapshots the note says "no version comparison is possible…".
Unknown source: 404.

### `/api/v1/history/{source_id}/compare`
Parameters:
- `baseline`, `comparison` (snapshot IDs of this source);
- `policy`: `same_date` with `date=`, or `different_dates` with `baseline_date=`
  and `comparison_date=`;
- `record_status`: `changed` (default), `all`, `added`, `removed`, `modified` or
  `unchanged`;
- `record_limit` (1–1000, default 100) and `record_offset` (≥ 0).

Example:
`/api/v1/history/synthetic_valley/compare?baseline=<v1>&comparison=<v2>&policy=same_date&date=2026-11-06&record_limit=100`

Response:
- `baseline` / `comparison` sides: `snapshot_id`, `content_sha256`, `retrieved_at`,
  `observed_coverage`, `service_date`, `date_in_coverage` and `reason`;
- `policy`, `synthetic`, `notices[]`, `identity_note`, `definitions`;
- `summary`: per metric `{baseline, comparison, delta, percent_change, status,
  percent_note, attributable_to_feed_version}`;
- `routes` / `stops` record pages: `counts` by status, `total`, `matching`,
  `offset`, `limit`, `records[]`, `truncated`, `next_offset`;
- `shapes.counts`.

Each record is `{id, status, changes[{field, baseline, comparison, distance_m?}],
baseline, comparison}`, keeping the **original values** of both sides. Route
records add `service` (trip templates on the date); stop records add `moved_m` and
`served_on_date`.

Errors:
- **404:** unknown source or snapshot.
- **422:** the snapshot belongs to another source ("snapshots of different
  operators are never compared"); the snapshot is not `ready`; the policy is
  missing or contradictory; an unknown `record_status`; or a limit/offset out of
  range.
- Comparing a snapshot with itself is allowed, with a `same_snapshot` notice:
  only the dates can differ.

## Comparison definitions

Static metrics come from the snapshot's files. Date-dependent metrics use the
GTFS **service day**: calendar plus `calendar_dates` exceptions, with times after
24:00 belonging to their service day.

| Metric | Definition |
|---|---|
| `route_records` | rows in routes.txt |
| `routes_with_trip_references` | route records referenced by ≥ 1 trip |
| `routes_scheduled` | routes with ≥ 1 trip active on the service day |
| `stop_records` | rows in stops.txt (records, not verified physical stops; never summed across operators) |
| `stops_referenced_by_trips` | stop records referenced by any stop_times row |
| `stops_served` | stop records called at by an active trip |
| `trip_templates` / `trip_templates_on_date` | rows in trips.txt / those active on the day |
| `exact_trip_instances` | 1 per non-frequency template + every departure of `exact_times=1` frequencies |
| `frequency_only_templates` | active templates whose only departures are `exact_times=0` windows |
| `non_exact_windows` | active `exact_times=0` windows (headways, no exact times) |
| `shape_ids` | distinct shape IDs; per-ID compared by exact point sequence |

- `percent_change` = 100 × (comparison − baseline) / baseline. It is **null when
  the baseline is zero**, with the note "Undefined: the baseline is zero."
- Metric status is `unchanged`, `changed`, or **`incomparable`** when either side's
  date is outside its calendar coverage. Values are then `null`, never 0.

**Record statuses** compare GTFS IDs within one source: `added`, `removed`,
`modified` or `unchanged`. Fields compared:
- routes: `agency_id`, `short_name`, `long_name`, `route_type`, `route_color`,
  and the distinct trip headsigns;
- stops: `name`, `location_type`, `parent_station`, and coordinates (moved
  > 1 m, haversine, with the distance reported).

**Identity limits**, stated in every response: an equal ID is a feed-record
match, not proof of the same physical route or stop. A new or missing ID is a
feed-record change, not proof that service or infrastructure appeared or
disappeared. Routes or stops of *different* operators are never matched, even
with equal IDs. Shape differences are source-geometry record differences, not
verified path changes. No geographic or heuristic route matching is attempted.

**Date policies:**
- `same_date`: one service day in both. Differences are attributable to the feed
  version only when the day is covered by both
  (`attributable_to_feed_version: true`). Otherwise there is a
  `not_comparable_on_date` notice and date-dependent metrics are `incomparable`.
- `different_dates`: always carries the `different_dates` notice ("Both the feed
  version and the service date differ … not attributable to the feed update
  alone, and this is not a timetable-revision comparison"), and
  `attributable_to_feed_version: false` on every date metric.

## UI: History view

The **History** button in the header (or `?view=history`) opens a full-width,
read-only view. The atlas stays mounted while hidden, so its filters, selection
and map state survive; **← Back to atlas** returns to it and resizes the map.
The view offers:

- **Operator picker.** It defaults to an operator that has two or more snapshots.
- **Coverage check date.**
- **Snapshot table:**
  - short ID with a "current atlas" tag, or the import status;
  - retrieval time, publisher version label and declared validity;
  - calendar coverage and whether the check date is covered ("No (service
    unknown)");
  - counts, archive presence, and validation (only if the hash matches).
- **One-snapshot note.** With fewer than two snapshots there is a plain note and
  no form.
- **Compare form:** baseline, comparison, the date policy (as radio buttons
  explaining each policy) and the dates. Comparing a snapshot with itself shows
  a caution. Results appear only after **Compare snapshots** is pressed; if the
  inputs change afterwards, the results stay visible but are labelled as being
  for the previous inputs.
- **Results:**
  - a synthetic banner when relevant, and a heading that names the policy;
  - side cards showing each service date, coverage and retrieval;
  - notices, and a summary table where the "% of baseline" column states the
    zero-baseline case and incomparable rows say so;
  - shape counts and the identity note;
  - paged route and stop record tables with a status filter. Changed fields are
    shown as `old → new`. Missing records read "no record", and "unknown" is used
    only for dates outside coverage;
  - the metric definitions.
- No map, markers or score are added in this milestone.

## Synthetic fixtures (`backend/tests/history_fixtures.py`)

Operator `synthetic_valley` ("SYNTHETIC Valley Transit"), two versions:

| | v1 (`2026.09`) | v2 (`2026.11`) |
|---|---|---|
| Calendar coverage | 2026-09-01 – 2026-12-31 | 2026-11-01 – 2027-03-31 |
| Exceptions | WK removed 2026-12-08 | WK removed 2026-12-25, added Sun 2026-12-27 |
| R1 | unchanged (2 weekday, 1 Saturday trip) | same |
| R2 | "Piazza - Station", 2 trips | "Piazza - Station via Quarry", new headsign, 3 trips |
| R9 / R10 | R9 only | R10 only |
| N1 | overnight 23:40 → 24:20 | same |
| FX / FW | exact 20-min frequency (3 runs) / non-exact 15-min window | same |
| Stops | P Q R S OLD | P R; Q renamed; S moved ~172 m; NEW |

A second operator, `synthetic_other`, reuses route `R1` and stop `P` with
unrelated content.

Expected results, all asserted by tests:

- **On 2026-11-06:** trip templates on the date 8 → 9 (+12.5%); exact instances
  9 → 10; frequency-only templates 1 → 1. Route records: 1 added, 1 removed,
  1 modified (`long_name`, `headsigns`), 4 unchanged. Stop records: 1 added,
  1 removed, 2 modified (Q `name`; S coordinates, 172 m).
- **On 2027-02-01**, which only v2 covers: baseline `null`, status
  `incomparable`, and notices `baseline_date_outside_coverage` and
  `not_comparable_on_date`.
- **On 2026-12-08 and 2026-12-27:** baseline 0, so `percent_change` is null
  ("Undefined").
- **Overnight trip:** N1's 24:20 call belongs to the Friday service day.
- **Re-importing v1's identical bytes** returns `unchanged`, adds no snapshot and
  leaves the selection as it was.
- **A failed snapshot with a missing archive** is listed, with `comparable: false`,
  and is refused for comparison.

## Tests and evidence

All runs were local on 2026-11-07; no GitHub Actions.

- **Baseline (589b0a7):** 120 passed, and the frontend build passed.
- **After, services stopped:** `pytest -q backend/tests` gives **134 passed,
  0 skipped**. That is 120 + 14 in `test_phase6_history.py`:
  - lifecycle and duplicate re-import;
  - selection unchanged after inventory and comparison;
  - one-snapshot sources;
  - archive hash verification;
  - failed snapshots with missing archives;
  - same-date counts and records;
  - out of coverage reported as incomparable, never 0;
  - exceptions and zero baselines;
  - the overnight service day;
  - the different-dates policy;
  - equal and invalid snapshots, mismatched sources and invalid policies;
  - source isolation with shared IDs;
  - paging and cache identity;
  - the API, with the current-atlas endpoints byte-identical before and after;
  - **one real read-only case**: each of the 4 real sources has 1 ready
    snapshot, it is selected, its archive is present, its validation matches by
    hash with 0 ERROR notices, and comparison is reported as impossible.
- **`verify_local.ps1 -Browser` on the real database** (log
  `verify-phase6-browser-20261107-132147.log`): Python 129 passed and 5 skipped.
  The skipped tests are the real-DuckDB cases, which cannot open the file while
  the backend holds its lock; they ran above with services stopped. Also:
  loopback startup 6.4 s; the build; the real-data smoke; the filter races; the
  Phase 5 quality check; and the new `smoke_history_ui.py` in **real mode**, where
  every operator shows a single current snapshot with "no version comparison is
  possible", and returning to the atlas leaves 349 routes and an unchanged
  selection. All passed.
- **`smoke_history_ui.py` in synthetic mode** (`build_history_demo.py`, served with
  `start_local.ps1 -Production -Database data/history-demo/history_demo.duckdb`)
  passed all 7 check groups:
  - inventory and coverage by date;
  - the same-date comparison: 8 → 9, +12.5%, R10/R2/R9 and NEW/OLD/Q/S with
    original values, and the status filter;
  - a date outside coverage shown as incomparable with "—", never 0;
  - the different-dates notice;
  - the same-snapshot notice, plus an injected 503 shown and recovered by Retry;
  - the one-snapshot operator explained;
  - back to the atlas with the selection unchanged.

  Screenshots: `phase6-history-same-date-*`, `phase6-history-different-dates-*`
  and `phase6-history-real-*` in ignored `data/local-verification/phase3/`.
- **Data safety:**
  - The SHA-256 of `data/transport_atlas.duckdb` is `3c308708…` before and
    after: unchanged since Phase 4.
  - `selection_members` is unchanged.
  - The content of every `data/raw/*/<sha>.zip` still matches its filename.
  - Synthetic data lives only in ignored `data/history-demo/`.

## Performance and caches

Atlas endpoints that Phase 6 does not change, medians of 5 (Phase 5 → Phase 6,
two runs):

| Endpoint | Phase 5 | Phase 6 |
|---|---:|---:|
| summary | 95 ms | 91 / 112 ms |
| active stops | 402 ms | 373 / 389 ms |
| regional geometry (cached) | 177 ms | 261 / 172 ms |
| ASPO timetable | 183 ms | 229 / 194 ms |
| stop board | 33 ms | 38 / 45 ms |

No regression; differences are run-to-run noise. A profile taken immediately
after the browser suites was briefly slower (summary 651 ms) and returned to the
values above once the machine was idle. Real-data smoke readiness was
4.8–10.0 s across runs (Phase 5: 5.4 s). The slow end follows the test suite
running in the same verification pass.

History endpoints:

| Request | Time |
|---|---:|
| `/history/sources` (real DB) | 32 ms |
| ARST inventory, cold / warm | 357 ms / 112 ms |
| ARST inventory with `verify_archives=true` (18.6 MB re-hash, cached afterwards) | 165 ms |
| ARST compared with itself, cold | 2.2 s (11.6 s before shape digests moved into SQL) |
| ARST compared, another page or a new date | 59 ms / 155 ms |
| ASPO compared with itself, cold | 242 ms |
| Synthetic inventory / comparison, cold / warm | 39 ms / 41 ms / 32 ms |

**Cost structure:**
- **Per-snapshot facts:** one grouped `stop_times` pass per snapshot (indexed by
  `snapshot_id`), cached for the life of the process.
- **Shape digests:** in DuckDB they are an SQL `md5(string_agg(... ORDER BY
  point_sequence))`, computed only for comparisons.
- **Date metrics:** `active_services` plus in-memory set operations. There is no
  per-interaction full scan.
- **Record lists:** bounded (`record_limit` ≤ 1000) and paged, with `total`,
  `matching`, `truncated` and `next_offset`.

As a cross-check, ARST compared with itself shows 0 changed records out of 322
routes, 3,450 stops and 2,418 shapes. Its 4,731 trip templates on 2026-11-06
equal the count independently verified in Phase 3.

## Limitations

- **Only one genuine version per operator exists locally.** Real comparisons of
  service volume, route and stop records, and coverage need a second imported
  archive for that operator.
- `import_file` still moves the operator's current selection to each new import.
  That was existing behaviour and is kept. In the synthetic database the current
  atlas therefore shows v2. Historical *browsing* never changes the selection.
- **Stop "served on date"** uses service days, not local wall-clock days.
- **Comparisons are record-level.** No physical stop or route matching, no
  geometry comparison beyond exact shape point sequences, and no map of either
  snapshot.
- Caches are in-process; a backend restart recomputes them (about 1 s per large
  snapshot).

## Safe procedure for importing additional real snapshots (needs separate authorization)

1. Get the user's explicit approval for the download and for which source.
2. Stop the services (DuckDB locks across processes), then back up
   `data/transport_atlas.duckdb`.
3. Download only from the allowlisted official URL with the existing HTTPS and
   provenance sidecar flow (`scripts/import_local_feeds.py`), into
   `data/downloads/`.
4. Import with `import_file`. It archives to `data/raw/<source>/<sha256>.zip`,
   refuses mismatched provenance and duplicates, and keeps older snapshots.
   Because it moves the current selection to the new snapshot, record the
   previous `selection_members` row first.
5. If the atlas should keep the previous version, restore that row explicitly (a
   reviewed, single-row update), then re-run the Phase 4 validator on the new
   archive.
6. Open History, choose the operator and compare with `same_date` on a date
   covered by both versions. Report retrieval time, version label, coverage and
   any `incomparable` metrics alongside the counts.

## Recommendations for the next phase

- With authorization, acquire a second official snapshot for one operator
  (Trenitalia publishes dated versions) and run the procedure above.
- Add an explicit, reviewed "set current snapshot" administrative action (CLI
  only, with backup), so imports need not change the atlas.
- Consider per-route timetable diffs (departure-time changes) and a two-snapshot
  map with strict snapshot-keyed sources, once real versions exist.

## Commits

Local only, on `codex/local-real-feed-atlas`. Not pushed: there is no PR, merge,
deployment or GitHub Actions run, and pushing needs the user's authorization.

| Commit | Change |
|---|---|
| `27ab6d6` | History backend, API, UI, synthetic fixtures, tests, browser check, docs |
| (this commit) | Records the commit hash in this document |

**Readiness:** the definition of done is met for synthetic data, and every local
check passed. Real comparisons need a second authorized import per operator.
