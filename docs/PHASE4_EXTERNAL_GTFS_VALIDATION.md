# Phase 4 — Local external GTFS Schedule validation

## Status

**Executed locally on November 7, 2026 (Windows 11, Java 22.0.2, validator
v8.0.1, validation date 2026-11-06). All four selected archives produced complete
reports with no system errors. No feed has an ERROR notice.** WARNING and INFO
notices exist for every operator; see [Results](#results-november-7-2026) below.
No notice revealed an importer defect. The DuckDB file's SHA-256 was identical
before and after every run. Feeds were not downloaded, reimported or modified.

This is an additive, read-only research step. It does not re-download transport
feeds, modify imported data, select different snapshots, trigger GitHub Actions,
or upload ZIPs to a web service. It operates only on the source ZIP chosen by
`selection_members` and `feed_snapshots` in the local DuckDB database.

## Version and integrity

- Canonical upstream: https://github.com/MobilityData/gtfs-validator
- Pinned CLI release: **v8.0.1** (published May 12, 2026)
- Official asset: https://github.com/MobilityData/gtfs-validator/releases/download/v8.0.1/gtfs-validator-8.0.1-cli.jar
- Published SHA-256: `19293ddd9b6f954f216d4f12054bd8a3232921751c4484339e339764a91000e2`
- Requires Java **17+**. The CLI uses `-i`, `-o`, `-c`, `-t`, and `-d`, as documented by MobilityData.
- The runner refuses to execute an unverified JAR, including files downloaded elsewhere.

The pinned version is intentional for reproducibility. Update the pin/digest and
rerun the offline tests when adopting a newer upstream release.

## Run on Windows (PowerShell)

First stop the dashboard, so the external read-only connection can access DuckDB:

```powershell
cd ([Environment]::GetFolderPath('Desktop') + '\TransportAtlas')
.\scripts\stop_local.ps1
java -version
```

If Java is missing or older than 17, install a Java 17+ runtime from a
reputable distributor such as https://adoptium.net/temurin/releases/ , then
reopen PowerShell and confirm `java -version`.

No downloads are needed for the first check:

```powershell
.\.venv\Scripts\python.exe scripts\validate_external_gtfs.py --dry-run
```

This reads the selected source rows from DuckDB, finds the archived ZIPs,
verifies SHA-256 and ZIP CRC, and prints each input path. It does not run Java.
If a path is missing, do not substitute a new or expired GTFS file silently.

Install the pinned JAR **once** and start with small operators:

```powershell
.\.venv\Scripts\python.exe scripts\validate_external_gtfs.py --sources aspo,atp_sassari --install-validator
```

This downloads **only the validator JAR**, not the GTFS feeds. If the download
is blocked, download the exact release JAR manually and place it at
`data/tools/gtfs-validator-8.0.1-cli.jar`; the script will still check its SHA-256.

Then validate the larger feeds without reinstalling or altering the database:

```powershell
.\.venv\Scripts\python.exe scripts\validate_external_gtfs.py --sources trenitalia_sardegna,arst
```

ARST may require significant RAM and processing time. Default JVM heap is 4096 MB;
pass `--heap-mb 2048` on smaller systems, or `--threads 1` if necessary. Per-feed
timeout defaults to 2400 seconds, configurable with `--timeout-seconds`.

## Reports and repeatability

All output stays under ignored
`data/local-verification/phase4-validator/`:

- `<source>-<SHA-prefix>-<service-day>-v8.0.1/report.json` — full validator notices
- `.../report.html` — human-readable report
- `.../system_errors.json` — technical failures, if any
- `.../validator.stdout.log`, `validator.stderr.log`
- `.../evidence.json` — archive SHA, source/snapshot, validator digest, status
- `summary-<UTC timestamp>.json` — high-level run index

The runner reuses a previously completed report for the same digest, version,
and date. Pass `--force` to rerun into a **new** folder while preserving old
reports. It never deletes provider archives or the DuckDB database.

`report_generated` means that the CLI returned, JSON/HTML output exists,
and no system error was reported. **It does not mean the feed passed validation.**
A GTFS feed can contain ERROR, WARNING and INFO notices even when the validator
process exits with code 0. Read the notices in the full report. Provider
noncompliance is not automatically an importer defect.

For reproducible calendar-related notices, the validation date is pinned to
`2026-11-06` by default. Use `--date YYYY-MM-DD` deliberately to compare dates;
include that date and the validator version when reporting counts.

## How to triage

For each operator, record total notices by severity and code, the affected
files/rows, and whether the problem is:

1. **Upstream feed defect or best-practice warning** (record it, do not rewrite provider ZIPs).
2. **Importer normalization loss** (compare native ZIP fields against database rows; add focused tests before changing schema).
3. **Uncertain applicability** (e.g. optional fields/metadata or historical validity; investigate before claiming error).
4. **Validator/system error** (inspect `system_errors.json` and logs; do not claim the source was validated).

Pay special attention to calendar exceptions, coordinates, route shape distances,
stop times, agency timezones, route/stop references, accessibility metadata,
frequencies and service-window notices. Do not assume these are faulty before
checking the actual notices and source files.

Review the reports and decide whether normalization of accessibility/platform
fields is justified. That would be a **separate migration phase** with a database
backup and a rollback plan, not part of this read-only audit.

## Acceptance criteria

- Run v8.0.1 on all four selected archived ZIPs without changing the database.
- Retain one evidence record and raw report per operator.
- Verify ZIP SHA-256 against the selected DuckDB snapshot and JAR against the
  official release checksum.
- Distinguish successful report generation, report notices, and validator failures.
- Summarize the most important NOTICE codes, source attribution, severity,
  count and likely implications for TransportAtlas's analyses.
- Only after reviewing actual reports, update project status and consider whether
  any importer or mapping behavior should change.

## Verification performed for the runner itself

The package arrived with seven offline tests, run in a Linux container without
the real archives. The first real Windows run exposed defects those tests could
not see; all are fixed, with regression tests (now 11 tests in
`backend/tests/test_external_gtfs_validator.py`):

1. **False `validator_failed` on every clean run.** v8.0.1 writes
   `system_errors.json` as `{"notices": []}`; the runner treated any non-empty
   object as failure. It now reads the `notices` list, and an unrecognized
   structure is `unverified`, never success.
2. **Batch aborted after the first feed** on a Korean-locale console (`cp949`
   cannot encode the em dash printed in the status line), leaving no summary.
   Output streams now use `backslashreplace`; validator output is decoded as
   UTF-8 explicitly.
3. **Evidence written to the wrong folder.** When a report folder already existed,
   the rerun went to a `-retry-` folder but `evidence.json` was written into the
   *original* folder, overwriting the earlier record and leaving the new report
   without one. This contradicted "preserving old reports". Fixed; the test now
   checks that each record sits beside its own report and earlier records are
   unchanged. (Consequence for this run's evidence: the first, falsely failed
   ASPO record was overwritten before the fix. ASPO was rerun with `--force`
   into `aspo-…-retry-fe37b111`, which has a correct record and identical
   notice counts.)
4. **Download failed TLS verification on Windows** (Python's bundled chain check
   rejected GitHub's chain). The downloader now uses `truststore` on Windows, the
   same policy as `transport_atlas.official`; certificate and hostname checks
   remain required. Redirects to non-HTTPS are now refused *before* the request
   is sent, not after it completes. The SHA-256 pin is unchanged.
5. **Java version parsing** could read a number from a `JAVA_TOOL_OPTIONS`
   banner, and read legacy `"1.8.0_x"` as major 1. It now anchors on the quoted
   version.
6. **Offline validation.** The CLI is now run with `-svu`, so the validator does
   not contact the network to check for newer versions.

## Results (November 7, 2026)

Reproduce the table with `.\.venv\Scripts\python.exe -I scripts\summarize_external_gtfs.py`;
it reads the latest correct evidence per source and writes
`analysis-<timestamp>.json` beside the reports.

| Operator | ERROR | WARNING (rules) | INFO (rules) | Validator time |
|---|---:|---:|---:|---:|
| ARST | 0 | 5,069 (12) | 73 (2) | 38.6 s |
| ASPO Olbia | 0 | 479 (8) | 0 | 14.6 s |
| ATP Sassari | 0 | 137,704 (5) | 5 (1) | 13.2 s |
| Trenitalia Sardegna | 0 | 97 (3) | 0 | 4.1 s |

Inputs: selected snapshots `52a339e0…` (ARST, ZIP `2a10d17c…`), `2fdf3861…`
(ASPO, `003278f3…`), `2f029bb3…` (ATP, `5398fc76…`), `92082781…`
(Trenitalia, `b27657fb…`). ZIP SHA-256 and CRC were verified against DuckDB
before each run. Validator JAR SHA-256 `19293ddd…a91000e2` matched the pin.
ATP's local recorded HTTPS provenance is verified; its original network
retrieval was not re-observed by this phase.

### Notices by rule, with classification

Classes: **P** = publisher data or best practice (record; do not rewrite ZIPs);
**A** = affects a TransportAtlas analysis or display; **I** = importer fidelity
checked against the database; **V** = validator heuristic/counting.

**ARST**

| Severity | Rule | Count | Class | Finding |
|---|---|---:|---|---|
| WARNING | mixed_case_recommended_field | 4,345 | P | All-caps names. Cosmetic. |
| WARNING | equal_shape_distance_same_coordinates | 557 | P, I, V | Duplicate consecutive shape points. Raw ZIP has 568 same-coordinate consecutive pairs (all with equal `shape_dist_traveled`); DuckDB has exactly 568. The validator flags 557; the 11 unflagged pairs (shapes 1800, 200, 2319, 733, 876, 930) are not explained by unused shapes. Simplifier output stays finite on these and on an all-duplicate line. No display effect. |
| WARNING | service_has_no_active_day_of_the_week | 85 | P, I | Services defined only via `calendar_dates.txt`. The importer applies exceptions; Phase 3 reconciled Nov 6 trips (4,731) against the raw ZIP. |
| WARNING | unused_shape | 46 | P, I | DuckDB also has exactly 46 shapes unreferenced by trips. Not drawn. |
| WARNING | expired_calendar | 20 | P | Services ended before 2026-11-06; not active on analysed dates. |
| WARNING | stop_without_stop_time | 6 | P, I, A | DuckDB has exactly 6 unused stop records. Excluded from the map's active stops, but listed by the proximity-only nearby-stops panel. |
| WARNING | route_short_name_too_long | 4 | P, A | e.g. `TCA` "Linea MONSERRATO-ISILI". Long labels in route lists. |
| WARNING | stop_too_far_from_shape | 2 | P, A | Trips `BSS_1`/`BSS_10`: stops M_8103 and M_8004 ("fermata ATP") 198 m and 355 m from the shape. Stop markers sit off the drawn line. |
| WARNING | missing_feed_contact_email_and_url, missing_feed_info_date, missing_recommended_field | 1 each | P | `feed_info.txt` lacks contact and `feed_end_date`. Validity must come from calendars, as the importer already does. |
| WARNING | route_long_name_contains_short_name | 1 | P | Route 513. Cosmetic. |
| INFO | big_gap_in_service | 72 | P | Seasonal services (e.g. FE10 inactive 2026-06-30 to 09-01). Not missing data. |
| INFO | unknown_column | 1 | P, A | `calendar.txt service_description`. Not imported; a possible human-readable service label. |

The importer's own `qa_issues` table records 156 ARST `missing_shape` warnings
(trips with no shape). The validator does not flag these because `shape_id` is
optional. They are the source of the existing "insufficient geometry" coverage
category, not a new finding.

**ASPO Olbia**

| Severity | Rule | Count | Class | Finding |
|---|---|---:|---|---|
| WARNING | fast_travel_between_consecutive_stops | 329 | P, A | 71 of 394 trips, mostly routes 08/10/12. 309 of the 329 consecutive-stop pairs are timed 1 second apart (e.g. trip 216987: 09:39:00 → 09:39:01 over 366 m). Interpolated or placeholder times, not real speeds. Matters for any future travel-time or speed analysis. |
| WARNING | mixed_case_recommended_field | 139 | P | All-caps names. |
| WARNING | trip_distance_exceeds_shape_distance_below_threshold | 4 | P | 1–2 m excess of `shape_dist_traveled`, which TransportAtlas neither imports nor uses. |
| WARNING | route_color_contrast | 3 | P, A | Routes 04/06/09 have white text on light colours. TransportAtlas does not import route colours yet. If it does, it must not trust `route_text_color` blindly. |
| WARNING | expired_calendar, leading_or_trailing_whitespaces | 1 each | P | Trailing space in "Via Viterbo, 9 ". The UI already normalizes whitespace (smoke-tested). |
| WARNING | stop_too_far_from_shape_using_user_distance, stops_match_shape_out_of_order | 1 each | P, A | Trip 217168, stops 152/153 (Via D'Annunzio) match the shape out of order and 115 m off. Shapes are drawn whole, not cut at stops, so only marker placement is affected. |

The feed's `feed_lang=it` appears in the report as "이탈리아어". That is the
validator's display name in the Korean JVM locale, not a feed value.

**ATP Sassari**

| Severity | Rule | Count | Class | Finding |
|---|---|---:|---|---|
| WARNING | missing_timepoint_value | 137,583 | P, I | Every one of the 137,583 `stop_times` rows. The column exists but is empty. DuckDB stores NULL in all 137,583 rows, a faithful copy. `timepoint` is not used by any query. Per GTFS, empty means exact times. |
| WARNING | stop_without_stop_time | 115 | P, I, A | DuckDB has exactly 115 unused stop records out of 1,032. Excluded from the map; listed by nearby stops. |
| WARNING | service_has_no_active_day_of_the_week | 4 | P, I | Calendar-dates-only services; Nov 6 (1,023 trips) reconciled in Phase 3. |
| WARNING | expired_calendar | 1 | P | Service `0004`. |
| WARNING | missing_recommended_file | 1 | P | No `feed_info.txt`; publisher, language and feed dates are unknown. |
| INFO | unsorted_stop_times | 5 | P, I | Rows not ordered by `stop_sequence`. Every TransportAtlas query orders by `stop_sequence`, so this has no effect. |

**Trenitalia Sardegna**

| Severity | Rule | Count | Class | Finding |
|---|---|---:|---|---|
| WARNING | mixed_case_recommended_field | 95 | P | All-caps headsigns. |
| WARNING | missing_feed_contact_email_and_url | 1 | P | |
| WARNING | trip_coverage_not_active_for_next7_days | 1 | V, A | The validator puts the "significant trips" window at 2026-06-06 to 2026-09-30. The feed itself (`feed_info` 2026-06-06 to 12-12, version dated 2026-06-05, `calendar_dates.txt` only) runs 200 trips on Nov 6, and DuckDB agrees. Daily trips peak at 310 in June and fall from 272 to at most 200 from October. The notice is a heuristic about reduced volume, not missing service. Still a real caveat: November analyses use a timetable published in June, and the October drop may be planned seasonal service or an incomplete projection. Only a newer official feed can tell which. |

### What this means for TransportAtlas

- **No importer defect was found.** Every notice tested against DuckDB matched
  the raw ZIP exactly: unused stops (6 / 0 / 115 / 0), unused shapes (46),
  duplicate shape points (568), NULL timepoints (137,583), Trenitalia Nov 6
  trips (200). Ordering issues are neutralized by `ORDER BY stop_sequence`.
- **No ERROR notices**, so no published feed is structurally invalid for the
  validated date. This does not prove that schedules are accurate.
- Findings with **analysis impact** (none change current counts or geometry):
  1. ASPO's 1-second inter-stop times invalidate naive speed or travel-time
     metrics. Any future travel-time analysis must detect them.
  2. Unused stop records (ATP 115, ARST 6) appear in the proximity-only nearby
     panel. Marking stops that have no `stop_times` would avoid suggesting service.
  3. Trenitalia's November service comes from a June-published feed with
     reduced autumn volume. Label it as such, or refresh with a newer official feed
     (download needs the user's decision).
  4. A few stops lie 115–355 m off their shapes (ASPO 1, ARST 2). Markers can
     appear off the line.
  5. Route colour contrast (ASPO) and `service_description` (ARST) are relevant
     only if optional fields are normalized later.

## Recommendations and remaining work

1. Keep the runner and summarizer as the repeatable compliance check. Rerun them
   whenever a snapshot changes; quote validator version, date and counts.
2. UI follow-up, no schema change: flag nearby-stop records that have no
   `stop_times` in their snapshot.
3. Before any speed or travel-time feature, add a check for sub-minute
   inter-stop times (ASPO) and treat NULL `timepoint` as exact per spec.
4. The optional-field migration (colours with contrast checks,
   `service_description`, accessibility, platforms) remains a separate phase
   with a database backup and rollback plan. It is not part of this read-only audit.
5. Decide whether to fetch a newer official Trenitalia feed. That needs the
   user's authorization and a reimport, so it is outside this phase.
6. Optional reproducibility tweak: pass `-Duser.language=en` to the JVM so the
   report's display strings do not depend on the machine locale. Not applied, to
   keep the reports from this run comparable.

Remaining blockers: none for this milestone. Integration (push, PR, merge) is
the user's decision.
