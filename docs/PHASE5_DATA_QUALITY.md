# Phase 5 — Data quality and reliability indicators

Baseline: `codex/local-real-feed-atlas` at `7bb2629` (Phase 4). The checkout matched
it at the start; only the unrelated untracked `PHASE4_APPLY.md` and
`codex-phase3f-review.patch` were present and remain untouched.

**Goal:** show users the known limits of the GTFS data without inventing a
reliability score. Every indicator is an explicit rule with its evidence, its
source operator and snapshot, and a stated reason. Provider values are never
changed or interpolated. No feed was downloaded or reimported, no schema changed,
and nothing is drawn on the map.

## What users see

All three inspectors render indicators through one component
(`frontend/src/Quality.tsx`, `QualityNotes`), grouped in a fixed order:

| Category | Meaning |
|---|---|
| Publisher data limitation | Something the publisher did not supply or did inconsistently |
| Missing or incomplete coverage | The data cannot show service for this date or record |
| Suspicious scheduled times | Published times that are implausible or contradictory |
| Schematic or uncertain geometry | Lines that are not the real path |
| Technical validation | The local MobilityData validator result |

Each item has a level: **info** (context; grey edge) or **caution** (take it into
account; amber edge), a title, a plain-language reason, optional examples, and
`source · snapshot`. Styling is deliberately restrained.

- **Route inspector:** route-level timing and geometry notes (`/api/v1/quality/route/{snapshot}/{route}`)
  and the source panel. Selecting a trip marks each stop reached by a questionable
  interval, with the exact published times and distance, and a one-line summary.
  The ordered-stop list now shows seconds whenever the published time has them
  (`06:57:01`), instead of rounding them away.
- **Stop inspector:** the stop's own service status and notes
  (`/api/v1/quality/stop/{snapshot}/{stop}?date=`); nearby stop records each carry
  a status badge and a tally (`/nearby?...&date=`).
- **Source inspector (new):** click a source in *Service coverage by source*. Also
  available, collapsed, inside the route and stop inspectors
  (`/api/v1/quality/source/{snapshot}?date=`).

## Indicator definitions

### 1. Stop service status (`quality.stop_service_status`)

For one feed snapshot and a GTFS **service day** (calendar + `calendar_dates`
exceptions, via the existing `active_services`):

| Status | Rule |
|---|---|
| `scheduled` | ≥1 trip calling here has a service active on the day |
| `not_scheduled_on_date` | Trips call here, none active that day, and the day is inside the feed's calendar coverage |
| `no_trip_references` | No `stop_times` row references the stop in this snapshot (any date) |
| `unknown` | The day is outside the feed's calendar coverage, so absence cannot be shown; or the record is a station/entrance (`location_type` ≠ 0) with no own calls |

Counts keep precision apart: `trip_templates` (trips whose pattern calls here),
`active_trip_templates`, `exact_calls_on_date` (each non-frequency call counts once;
`exact_times=1` frequencies are expanded), and `frequency_templates_on_date`
(`exact_times=0`; headway window only, never counted as exact departures).
Source-specific stop IDs are shown beside every nearby record. Unused records are
**listed, never hidden**. The proximity disclaimer is unchanged: nearness is not a
verified physical-stop match or a passenger transfer.

GTFS fields: `stops.location_type`, `stop_times.stop_id/trip_id`, `trips.service_id`,
`calendar.*`, `calendar_dates.*`, `frequencies.exact_times/headway_secs`.

Evidence: 115 ATP Sassari and 6 ARST records have no trip references. That is
the same set Phase 4's validator reported, re-checked by a regression test
against the local database.

### 2. Stop-to-stop interval quality (`quality.segment_intervals`)

Reusable for future travel-time analysis. Input: one trip's stop times in
`stop_sequence` order, as imported (GTFS service-day seconds, so `24:01:00`
correctly follows `23:59:00`; nothing wraps at midnight).

For each consecutive pair:

- `elapsed = arrival(next) − departure(previous)`, falling back to the other
  time at each stop. Pairs with a missing time are counted as untimed, not guessed.
- `resolution` = the largest of 60/30/15/10/5/1 s that divides both times.
  Rounded times can understate the true gap by up to that step, so it is added.
- `d` = haversine distance between the two stop records (**straight-line**). The
  vehicle's real path is at least as long. Shape distances are not imported, so
  route distance is not available, and the result always says
  `distance_basis: straight_line`.
- `min_implied_speed = d / (elapsed + resolution)`, a **lower bound** on the speed
  the published times imply.

Classes:

| Class | Rule | Meaning |
|---|---|---|
| `implausible` / `min_implied_speed_exceeds_threshold` | lower bound > mode threshold | Suspicious, not proven: the stop coordinates or the times could be at fault |
| `implausible` / `near_zero_interval` | elapsed ≤ 1 s, resolution 1 s, d ≥ 50 m | Placeholder-like spacing |
| `inconsistent` / `time_decreases` | elapsed < 0 | Published values contradict each other (GTFS requires non-decreasing times) |
| `inconsistent` / `departure_before_arrival` | departure < arrival at one stop | Same |

Thresholds are MobilityData's `fast_travel_between_consecutive_stops` values by
`route_type` (bus 150, rail 500, tram/light rail 100, ferry 80 km/h, …; 200 km/h
for unlisted types). The trip response (`/api/v1/trip/...`) now includes
`timing`. Route and feed summaries count **trip templates**. Frequency-based
trips are analysed once as templates and labelled as such; non-exact windows stay
separate. Scope is explicit: a route note says "N of M trip templates on this
route" and "Other trips are not affected"; nothing labels a whole operator unreliable.

Real-data evidence (validation date 2026-11-06 data, all trip templates):

| Feed | Templates | Intervals checked | Implausible | Inconsistent |
|---|---:|---:|---:|---:|
| ASPO | 394 | 14,197 | 310 (67 templates, 7 routes) | 0 |
| ARST | 5,565 | 86,025 | 0 | 0 |
| ATP Sassari | 3,404 | 134,179 | 0 | 0 |
| Trenitalia | 796 | 4,025 | 0 | 0 |

ASPO's 310 = all 309 one-second pairs found in Phase 4 plus one 20-second
interval; 263 were flagged by the speed bound (including the 20-second one) and
47 by the near-zero rule. Known case, a regression
test: trip 216987, stop 873 `09:39:00` → stop 871 `09:39:01`, 366 m, ≥ 658 km/h.
ASPO route 08: 19 of 41 trip templates, 163 intervals.

**False-positive risks and how they are limited**

- *Rounded times.* A first version flagged 16 ARST intervals. ARST publishes
  half-minute times, so equal `:30` times mean "within 30 s". The resolution rule
  (largest dividing step) removed all 16; a regression test pins trip `827_3`.
- *Wrong stop coordinates* produce a long straight-line distance and a false
  high speed. Hence "suspicious, not proven".
- *Pass-through records* (a stop record the vehicle does not serve) could be
  timed 1 s apart legitimately. The near-zero rule needs ≥ 50 m to reduce this.
- *Lenient side:* adding the rounding step means some real errors in rounded
  feeds go unflagged (e.g. the validator also reported 30-second intervals of
  ~1.3 km for ASPO that this rule accepts). That trade-off is intentional: the
  indicator should not cry wolf.

### 3. Feed freshness and coverage (`quality.source_quality`)

Five things are shown separately and never substituted for one another:

| Field | Source | Notes |
|---|---|---|
| Local copy retrieved | `feed_snapshots.retrieved_at` | Download time of this local copy; explicitly *not* a publication date |
| Publisher version label | `feed_info.feed_version`, verbatim | "Unknown (no feed_info.txt)" or "Not supplied" when absent |
| Date in version label | A `YYYYMMDD` token in that label | Reported as a label. ARST's `01112026` is *not* read as a date |
| Publication date | — | Always "Unknown": GTFS has no publication-date field |
| Declared validity | `feed_info.feed_start_date/feed_end_date` | "Not declared" / "Unknown (no feed_info.txt)" |
| Calendar coverage | `feed_snapshots.feed_start/feed_end` | Dates the service calendars cover |
| Selected date | calendars + exceptions | Trip templates scheduled that service day, and the busiest same weekday in the feed |

Coverage notes: date outside the calendars (caution: "absence here is not absence
of service"); date outside declared validity (caution); calendars cover the date
but schedule nothing (info).

**Projection caution** (`long_range_reduced_volume`) appears only when all of
these hold:
- the version label contains a date;
- the selected date is more than 90 days later;
- that date's trip templates are at most 75% of the busiest same weekday in the feed.

Comparing the same weekday avoids weekday/weekend effects. The text says
seasonal timetables often differ and that reduced volume alone does not show
missing service. Today it triggers only for Trenitalia:
- version label dated 2026-06-05, 154 days before 2026-11-06;
- 200 trip templates, 65% of the busiest Friday (310 on 2026-06-12).

ASPO and ARST have no dated label, and ATP has no `feed_info.txt`, so they get no
such caution. They do get the publisher notes "No declared end date" (ARST) and
"No feed_info.txt" (ATP).

### 4. Technical validation

The source panel reads the latest local MobilityData report for **exactly this
archive** (matched by SHA-256, with the evidence stored beside its report, status
`report_generated`). It shows the ERROR/WARNING/INFO totals and states that zero
ERROR notices means structurally valid GTFS, not that schedules match real-world
operations. Without a matching report it says so. Reports stay in the ignored
`data/local-verification/phase4-validator/`; the API only reads them.

### 5. Geometry

Route notes count trip templates without a usable source shape ("drawn as dashed
straight links … schematic"). This complements the existing coverage panel and
the dashed map style. Stops far from their shape (Phase 4: ASPO 1, ARST 2) are
not yet indicated; that needs projection onto shapes (see limitations).

## Tests and results

All runs were local, on November 7, 2026.

- **Python, services stopped:** `pytest backend/tests` gives **120 passed**. That is
  the 102 earlier tests plus 18 in `backend/tests/test_phase5_quality.py`:
  - synthetic interval rules: overnight `24:01` vs a wrapped `00:01`, half-minute
    rounding, the near-zero rule, rail thresholds, and inputs left unchanged;
  - stop statuses, including calendar exceptions, frequencies with exact and
    non-exact times, and a station record;
  - daily volume;
  - freshness, where retrieval is never treated as publication and a missing
    `feed_info` is reported as unknown;
  - the projection caution: present only with a dated label, an age over 90 days
    and reduced volume;
  - a validation record matched only to the exact archive;
  - all API endpoints, including 404s;
  - **4 real-feed cases**: ASPO trip 216987 and the 310/67 totals, ARST trip 827_3
    not flagged, 115/6/0/0 unused stops, and the Trenitalia caution present on
    Nov 6 but absent on Jun 12.

  The real-feed cases skip cleanly when the DuckDB file is locked by a running
  backend (116 passed, 4 skipped inside `verify_local.ps1 -Browser`).
- **`verify_local.ps1 -Browser`** with services running (log
  `data/local-verification/phase3/verify-phase5-browser-20261107-123612.log`):
  - Python tests;
  - loopback startup (4.2 s);
  - TypeScript/Vite production build;
  - the real-data smoke (all six check groups; map ready in 5.4 s);
  - the filter-race suite;
  - the new `scripts/smoke_quality_ui.py`. All passed.
- **`smoke_quality_ui.py`** compares what Chrome renders with the API:
  - the map still has 349 routes and exactly the active stops, with no new markers;
  - Trenitalia's source inspector: every freshness field, its indicators, and the
    caution text;
  - ATP: "Unknown (no feed_info.txt)" and no projection caution;
  - ASPO route 08: "19 of 41"; trip 216871 has 9 flagged stops and visible `06:57:01`;
  - an ATP stop's nearby badges match the API exactly; the unused `ATPSS_10` opens
    with status `no_trip_references` and its source ID shown;
  - no severe console errors.
- **Manual inspection** in the app's browser pane and screenshots
  (`phase5-source-trenitalia-*`, `phase5-trip-flags-*`, `phase5-unused-stop-*`):
  indicators are grouped, muted and inspector-only.
- The DuckDB file's SHA-256 is unchanged (`3c308708…`). Raw ZIPs, archives,
  caches and validator reports were preserved.

## Performance

Existing endpoints that Phase 5 does not change were re-profiled
(`after-phase5-http.json`, median of 5): summary 95 ms, active stops 402 ms, cached
regional geometry 177 ms, ASPO route timetable 183 ms, stop board 33 ms. These match
the levels after the overnight latency work; no regression. Browser readiness in
the real-data smoke was 5.4 s, within the earlier warm range of 3–6 s.

New or extended work (medians of 5 unless noted):

| Request | Time |
|---|---:|
| Nearby, without → with `date` (ASPO / ATP stop) | 26 → 42 ms / 22 → 60 ms |
| Trip with `timing` (ASPO 216871) | 33 ms |
| Route quality, all 389 routes, one pass each | median 59 ms, max 342 ms (ATP 8) |
| Stop quality | 27 ms |
| Source quality, warm | 33 ms |
| Source quality, first ever per snapshot (feed-wide scan, then disk cache) | ARST 2.9 s, Trenitalia 0.5 s |

Quality data loads in separate requests from timetables and boards, so it never
delays them. The one-off feed-wide scan happens only when a source panel is first
opened for that snapshot. Stop status for very large stop lists scans the
snapshot once instead of using a huge `IN` list. That cut a full four-feed
classification from about 2 minutes to about 4 seconds.

## Remaining limitations

- Route distance along the shape is not available (`shape_dist_traveled` is not
  imported and the schema is frozen in this phase), so interval checks use
  straight-line lower bounds only. Stops far from their shape are not flagged.
- Stop status is per GTFS service day, not per local wall-clock date: a call after
  midnight belongs to the previous service day, as in the stop board's model.
- Stops are only matched by proximity; physical-stop identity across sources is not
  established.
- The validation summary is read when first requested and cached per process; a
  newer report appears after a backend restart.
- The feed-wide timing summary is computed once per snapshot (about 3 s for ARST/ATP)
  and then stored in `data/cache/quality/` (rule-versioned; safe to delete).
- Unused stop records are reachable only through nearby lists (they are not map
  markers or search results, both of which list active stops). That is unchanged
  and intentional.
- Thresholds are MobilityData's defaults; they are not calibrated per operator.

## Commits

Local only, not pushed (pushing needs the user's authorization). All commits skip CI.

| Commit | Change |
|---|---|
| `1d231ed` | Phase 5 indicators, API, inspectors, tests, browser check, docs |
| (this commit) | Records the commit hashes in this document |

**Integration readiness.** Every local check passes: tests, build, both
existing browser suites, the new quality check, and manual inspection. Existing
routes, stops, variants and timetable instances are unchanged. The branch is ready
for the user's review. Pushing, opening a PR and merging remain the user's decisions.
