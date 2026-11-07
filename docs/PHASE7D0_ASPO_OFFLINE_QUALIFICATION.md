# Phase 7D0 — Offline qualification of the acquired historical ASPO feed

**Date:** 2026-11-07. Work was offline, read-only on all real data, with a documentation-only commit.
**Baseline:** branch `codex/local-real-feed-atlas` at HEAD `6b90431769933f73aac425aa8b2a876cf29770ec`, equal to
origin. The worktree was clean apart from the two known untracked files (`PHASE4_APPLY.md`,
`codex-phase3f-review.patch`), which were left untouched. No backend or frontend services were
running.

What was not done:
- **Nothing was downloaded.** No account, cookie, token, API key or browser profile was accessed.
  The only web reads were public Transitland documentation pages, with no sign-in.
- **No real data was written.** Nothing was written to `data/transport_atlas.duckdb`, `data/raw`
  or `selection_members`, and nothing was activated.
- **The real History screen is unchanged.** It still shows **one** ASPO snapshot, the current one.
  The comparison in §4 ran only in a disposable fixture database.

## 1. Evidence identity

| | value |
|---|---|
| File | `data/downloads/archive/transitland/aspo/f-spqb-aspoolbia-b48388a26eeffbadde634b526dc3cb1acd8606ab.zip` (the only file in that folder; no sidecar) |
| Bytes | **835 773**. Expected 835 773: **match** |
| SHA-1 | **`b48388a26eeffbadde634b526dc3cb1acd8606ab`**. Transitland version id: **match** |
| SHA-256 | **`372e851c6e9f3b94a98d4dedaf3419ff92338bcc6ce6f144c24015c5b11d4715`**. User-reported value: **match** |
| Independent check | Python `hashlib` and Windows `certutil -hashfile` give identical SHA-1 and SHA-256 |
| File time | modified 2026-11-07 20:21:10 +09:00 (11:21:10 UTC), when the user's browser saved it. Unchanged by this phase |
| Public record | `https://www.transit.land/data/feeds/f-spqb-aspoolbia/versions/b48388a26eeffbadde634b526dc3cb1acd8606ab`, which shows "Fetched 2026-08-28" (**date only**; time of day not public without an account) |

**Evidence copy.** A hash-verified, read-only copy was made at
`data/local-verification/phase7d0/evidence-backup/f-spqb-aspoolbia-b48388a26eeffbadde634b526dc3cb1acd8606ab.zip`
(SHA-256 identical). It was copied with `shutil.copy2`, which keeps the modification time.

**Inventory and manifests**, all git-ignored, under `data/local-verification/phase7d0/`:
- `evidence-inventory.json` records the hashes, size, path, record URL, observation basis,
  acquisition method `authenticated_browser`, delivery URL *unknown*, and the UTC verification time;
- `SHA256SUMS` and `SHA1SUMS` list the original and the copy.

The original file's bytes, name and modification time are unchanged: re-hashed at the end of the
phase, and the modification time was checked again.

**Recovery plan for external evidence.** A DuckDB backup alone cannot rebuild the archives or the
provenance files.
- Keep together, under one folder:
  - the downloaded ZIP;
  - its future provenance record;
  - `evidence-inventory.json`;
  - both `SUMS` manifests.
- Copy that folder to a second disk or medium before any real import.
- After a real import, also copy `data/raw/aspo/<sha256>.zip` and `<sha256>.zip.provenance.json`,
  and append both to `SHA256SUMS`.
- **To recover:** restore the files, run `sha256sum -c SHA256SUMS` and `sha1sum -c SHA1SUMS`, then
  restore the database from its `manage_snapshots.py` backup (`verify-backup`, then
  `restore-backup` to a new path).
- A ZIP whose hash does not match must not be used.

## 2. GTFS integrity (actual contents)

Commands below run from the repository root, with `S=data/local-verification/phase7d0/scripts`.

```text
python -I $S/gtfs7d0.py . historical_b48388a2 <historical zip> data/local-verification/phase7d0/gtfs-historical-b48388a2.json
python -I $S/gtfs7d0.py . current_a710bc7c data/raw/aspo/003278f3…b382.zip data/local-verification/phase7d0/gtfs-current-a710bc7c.json
```

- **ZIP CRC:** OK for both files.
- **Parsing:** the project's `read_feed` + `_validate` parse both without error.
- **Unchanged inputs:** SHA-256 is unchanged after reading.

| table | historical `b48388a2` | current `a710bc7c` |
|---|---|---|
| agency | 1 | 1 |
| routes | **17** | 15 |
| stops | **423** | 422 |
| trips | 496 | 394 |
| stop_times | 18 461 | 14 591 |
| calendar | 18 | 18 |
| calendar_dates | 55 (40 removals, 15 additions; 2026-10-04 to 2026-12-26) | 45 (33 / 12; 2026-11-01 to 2026-12-26) |
| shapes (points) | 44 545 | 37 414 |
| frequencies / transfers | 0 / 0 | 0 / 0 |
| `feed_info` | ASPO S.p.a, version `1`, **2026-08-27 → 2026-12-31** | ASPO S.p.a, version `1`, 2026-10-27 → 2026-12-31 |
| calendar envelope | 2026-08-27 → 2026-12-31 | 2026-10-27 → 2026-12-31 |

- **Matches Transitland's listing.** 17 routes and 423 stops equal Transitland's figures for
  `b48388a2`. This only corroborates the file; the hash match in §1 is the identity proof.
- **Referential anomalies: none** in either file. There are no trips with an unknown route or
  service, no trips without stop times, no unknown stops, no missing shapes, no stops without
  coordinates and no duplicate IDs.
- **`feed_version` = `1` in both files.** The publisher's version label does not distinguish
  them.

## 3. Service on Friday 2026-11-06 (independent calculation)

The calculation uses only the standard library. A service is active if its `calendar.txt` weekday
flag and date range include the day. `calendar_dates.txt` then takes precedence: type 1 adds a
service, type 2 removes it. Trip times past 24:00 were checked in both directions.

| | historical `b48388a2` | current `a710bc7c` |
|---|---|---|
| active service IDs | 9 (all from calendar; no exceptions on the date) | 10 (adds `AN-FER_2`, valid 2026-11-02 → 12-31) |
| **trips scheduled** | **319** | **319** |
| routes with trips | **15** | **15** |
| stops served | **421** | **421** |
| per-route trips | 01:62 02:62 03:11 04:21 05:12 06:11 07:22 08:41 09:17 10:29 11:19 12:8 1S:1 2S:1 4S:2 | identical |
| trips of this day past 24:00; previous-day trips running into this date | 0; 0 | 0; 0 |

The older feed **does** have real service on 2026-11-06, so the date is valid for a same-date
comparison and was not changed.

## 4. External validation (MobilityData GTFS Validator 8.0.1, offline)

The JAR `data/tools/gtfs-validator-8.0.1-cli.jar` was already present, with SHA-256
`19293ddd…00e2` equal to the pin in `scripts/validate_external_gtfs.py`. Nothing was downloaded or
installed.

```text
java -Xmx2048m -jar data/tools/gtfs-validator-8.0.1-cli.jar -i <zip> \
  -o data/local-verification/phase7d0/validator-<label> -d 2026-11-06 -c it -t 2 -svu
```

| | historical `b48388a2` | current `a710bc7c` |
|---|---|---|
| exit code / system errors | 0 / **0** | 0 / **0** |
| ERROR | **0** | **0** |
| WARNING | 19 113 | 479 |
| INFO | 0 | 0 |

**Historical warnings:**

| warning | count | note |
|---|---|---|
| `missing_timepoint_value` | 18 461 | every stop time; the older feed has no `timepoint` column |
| `fast_travel_between_consecutive_stops` | 374 | |
| `mixed_case_recommended_field` | 244 | |
| `trip_distance_exceeds_shape_distance_below_threshold` | 15 | |
| `stop_too_far_from_shape_using_user_distance` | 10 | |
| `route_color_contrast` | 4 | |
| `expired_calendar` | 2 | the summer services `ES…15-06_15-09` |
| `stops_match_shape_out_of_order` | 2 | |
| `leading_or_trailing_whitespaces` | 1 | |

The current feed has the same kinds of warnings in smaller numbers, except
`missing_timepoint_value`.

## 5. Isolated same-date comparison

This is an **isolated analysis fixture, not a production, provenance-compliant historical import.**

```text
python -I data/local-verification/phase7d0/scripts/compare7d0.py .
```

**Fixture database:**
`data/local-verification/phase7d0/isolated-compare/ISOLATED-ANALYSIS-FIXTURE-not-production.duckdb`
(git-ignored and disposable).
- The current ZIP was imported first and selected, with its existing official record copied from
  `data/raw`, which was only read. It classifies as `publisher_direct`.
- The historical ZIP was imported second, with `activate=False` and **no provenance record**, so it
  honestly classifies as `unknown_legacy`.
- Selection in the fixture after both imports: `aspo` → `2fdf386102741d57c5045190da363097`, the
  current snapshot.
- The real database, the current archive and the downloaded ZIP were hashed before and after:
  unchanged.

`SnapshotHistory.compare(policy='same_date', date=2026-11-06)` with baseline = historical,
comparison = current. Raw output is `comparison-same-date-2026-11-06.json`; a digest is in
`summary.json`.

| metric | historical | current | status |
|---|---|---|---|
| trip templates on date / exact instances | 319 / 319 | 319 / 319 | unchanged |
| routes scheduled on date | 15 | 15 | unchanged |
| stops served on date | 421 | 421 | unchanged |
| route records | 17 | 15 | −2 |
| stop records | 423 | 422 | −1 |
| trip templates (all dates) | 496 | 394 | −102 |
| shape IDs | 79 | 68 | −11 |

**Record-level differences:**
- **Routes:** 2 removed, 2 modified, 13 unchanged.
  - Removed: `05PI` (Porto Istana – Olbia) and `05PR` (Porto Rotondo – Olbia). Both had 0 trips on
    the date in the older feed, because they belong to the summer services that ended 2026-09-15.
  - Modified: `long_name` of `12` and of `2S` changed.
- **Stops:** 1 removed (`1059`, not served on the date in the older feed).
- **Shapes:** 43 added, 54 removed, 16 modified, 9 unchanged (by shape ID).
- **Notices:** none.

**Agreement with §3:** all on-date metrics agree exactly with the independent calculation.

**A finer check that History's metrics cannot show.** The independent trip-level comparison of
running trips covered trip IDs, routes, services, stop sequences and times.
- 317 of the 319 running trip IDs are shared.
- Their stop sequences and times are **identical**. The only field that differs is `shape_id`,
  which changed on 295 trips (shapes were renumbered).
- **Two trips were replaced one for one**, so the per-route counts stay equal:
  - **route 06**, 06:25 departure (`217185` → `217496`): now 76 stops instead of 72, arriving
    07:23:10 instead of 07:20:55;
  - **route 11** (`217216` → `217497`): departure moved from 13:00 to **13:05**, with the same stop
    sequence shifted by 5 minutes.

  Both new trips use the new service `AN-FER_2`.

**Interpretation.** Between the 2026-08-28 and 2026-10-27 archive versions, the scheduled service
for Friday 2026-11-06 is the same in volume. On that date, two trips in the published timetable
changed:
- one has a longer stop pattern (route 06);
- one has a 5-minute shift (route 11).

The 17 → 15 route and 423 → 422 stop drops are **GTFS record removals**: two summer-only routes and
one stop with no service on the date. They are not evidence of a real-world route closure.

History's count-based summary reports this as "unchanged" for trips on the date. A future History
improvement could add a per-trip timetable diff (separate phase).

## 6. Provenance: known and unknown

| fact | status |
|---|---|
| Bytes are Transitland version `b48388a2…` | **Known**: computed SHA-1 equals the version id |
| SHA-256 / size | **Known** (§1) |
| Original publisher | ASPO S.p.a, per `feed_info`; Transitland records `https://www.aspo.it/wp-content/uploads/open_data.zip` as the URL *it* fetched from. That is the archive's claim and **not our download origin** |
| Archive observation date | 2026-08-28 (**date only**), from the public record page. The time of day is not publicly visible |
| Acquisition | The user, via a browser, with their own Transitland Hobbyist/Academic account; file saved 2026-11-07 11:21:10 UTC |
| Actual delivery URL / host of the browser download | **Unknown.** Not recorded, not inferred, and not looked up in browser history or cookies |
| Documented provider endpoint | Transitland's public docs (read 2026-11-07) say historic versions download from `https://transit.land/api/v2/rest/feed_versions/{feed_version_key}/download`, an API-key endpoint, and that downloads require a plan that includes them and a licence that does not forbid redistribution. They do **not** say what the website's "Download GTFS" button requests or which host finally delivers the bytes |
| Licence | CC BY 4.0, attribution required (region open-data page and Transitland listing, Phase 7B) |

**Consequence for Phase 7C.** The `third-party-archive-provenance/1` schema requires an
`archive.download_url` on a registered provider download host, plus an optional
`final_download_url`. Transitland is registered with **no download hosts** and
`import_enabled: false`. Writing `https://transit.land/api/v2/rest/feed_versions/b48388a2…/download`
into a record would be a **guess**: the user did not use the API, and redirects are unknown.
So a valid record cannot honestly be written today, and the real import is blocked by design. That
is the intended outcome; no safeguard was bypassed.

**Proposed future mode (design only; not implemented, needs review): `browser_acquired` archive
provenance.**
- **Anchors:**
  - (a) the public version record URL on a registered record host;
  - (b) the computed SHA-1 equal to the version id in that URL;
  - (c) the local SHA-256 and size.
- **Fields:**
  - `archive.acquisition_method = "authenticated_browser"`;
  - `archive.download_url = null`, with `archive.delivery = "unknown"`;
  - `archive.observed_on = "2026-08-28"`: a date-only field, rather than inventing a time for
    `observed_at`;
  - `retrieved_at` = when the file was saved locally;
  - plus a statement that the user's own plan and account were used. No account identifiers.
- **Rules:**
  - allowed only for a provider entry that explicitly sets `browser_acquisition: true`;
  - requires the SHA-1 anchor;
  - must never be described as downloaded directly from ASPO;
  - History would label it "Archived copy from Transitland (downloaded via the user's browser;
    delivery host not recorded)".
- **Constraints:** the official-host allowlist and the existing API-download mode stay unchanged,
  and no signed or opaque host is added to any allowlist.
- **Code to change** (separate reviewed phase): `archive_provenance.archive_problems`, `classify`,
  the provider registry, tests, and the History label.

## 7. GO / NO-GO for a future non-activating real import

| gate | state |
|---|---|
| Bytes identical to Transitland `b48388a2…` (SHA-1), SHA-256 and size recorded | **GO** |
| CRC, project parser, external validator (0 ERROR, 0 system errors) | **GO** |
| Real service on 2026-11-06 in both versions | **GO** (319 / 319 trips) |
| Evidence copy plus manifests made; second-medium copy | **Partly**: an on-disk copy exists. **The user must make an off-disk copy** |
| Honest provenance record that passes the existing checks | **NO-GO**: browser delivery URL unknown; needs the reviewed `browser_acquired` mode (§6) or verified provider hosts |
| Transitland provider enabled in a reviewed change | **NO-GO** (still disabled) |
| Database backup | Automatic with `manage_snapshots.py import … --real-database --apply`, after reviewing the preview |
| Explicit user approval for `--real-database --apply` | **Not given** |
| Selection invariant afterwards (`aspo` → `2fdf3861…`; `feed_sources.download_url` for aspo stays `https://www.aspo.it/wp-content/uploads/open_data.zip`) | to verify after the import |
| Final same-date comparison in the real History screen, with attribution | after the import |

**Decision: NO-GO for a real import now.**

**Next approval needed:** decide whether to commission a separate, reviewed Phase 7D1 that adds the
`browser_acquired` provenance mode (§6) and enables Transitland for that mode only. After it lands,
the preview, the `--real-database --apply` import (non-activating) and the History comparison each
need explicit approval.

## 8. Final state checks

| | value |
|---|---|
| Real database SHA-256 | `3c308708af91052c06a5ed4479ec6b0d5332a71c056a9d25d8215da322523ca0`, before and after; modification time 2026-11-06 17:33:51 +09:00 unchanged |
| Real selection (read-only check) | arst `52a339e0…`, aspo `2fdf3861…`, atp_sassari `2f029bb3…`, trenitalia_sardegna `9208278…`. 4 snapshots |
| ASPO source download URL | `https://www.aspo.it/wp-content/uploads/open_data.zip`, unchanged |
| `data/raw/aspo` | unchanged: one archive plus its official record |
| Downloaded ZIP | preserved; SHA-256 and modification time unchanged |
| Committed | this document only. All evidence, fixture databases, reports and the ZIP copy remain under git-ignored `data/` |
