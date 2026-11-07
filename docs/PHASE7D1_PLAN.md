# Phase 7D1 — browser-acquired archive provenance

**Status:** implemented as planned on 2026-11-07, after approval ("Yes, implement"). Decision A:
Transitland's browser mode is **enabled**. Decision B: `retrieved_at` defaults to the file's
modification time. Results are in §8. The real ZIP, the real database and `data/raw` were not
touched, and no real record was written.
**Starts from:** `3478b9e` (Phase 7D0) on `codex/local-real-feed-atlas`.
**Why:** Phase 7D0 verified that the user's browser-downloaded ZIP is Transitland version
`b48388a2…`. Phase 7C's archive record cannot honestly describe it, because it requires an
`archive.download_url` on a verified provider download host, and the browser's actual delivery URL
is unknown. 7D1 adds a mode that records what *is* known and marks the rest as unknown.

## 1. Scope and boundaries

**In scope:**
- code, configuration, tests and docs;
- synthetic data and disposable databases only;
- a documentation and code commit, made locally.

**Out of scope, each needing its own approval later (§7):**
- writing a provenance record next to the real ZIP;
- any `--real-database` command;
- the real comparison;
- any push.

**Unchanged:**
- the official-source path and its host allowlist;
- Phase 7C's API-download archive mode;
- preview-by-default, backups, `--real-database`, staging, the expected SHA-256 check;
- non-activating imports and the selection invariants;
- the database schema (still no table or column; startup cannot migrate).

## 2. Record design

The record extends `transport-atlas/third-party-archive-provenance/1` with one discriminator,
`archive.acquisition_method`. If it is absent, the default is `provider_download`, so every existing
record validates exactly as today.

| field | `provider_download` (Phase 7C, unchanged) | `authenticated_browser` (new) |
|---|---|---|
| `archive.download_url` | required, HTTPS on the provider's download hosts | **must be absent or null** |
| `archive.final_download_url` | optional, same host rule | **must be absent** |
| `archive.delivery` | — | **required, exactly `"unknown"`** |
| `archive.record_url` | HTTPS on a record host, contains `version_id` | **must equal the provider's record-URL pattern exactly**, e.g. `https://www.transit.land/data/feeds/{feed_id}/versions/{sha1}` with the computed SHA-1 |
| `archive.feed_id` | — | required, e.g. `f-spqb-aspoolbia`; must appear in `record_url` |
| `archive.version_id` | 40-hex SHA-1 equal to the computed SHA-1 | same: **this is the anchor** |
| observation time | `observed_at` (timezone-aware) + evidence URL | `observed_at` as before, **or** new `observed_on` (a date `YYYY-MM-DD`, not after the local retrieval date) when only a date is public |
| `retrieved_at` | when this computer obtained the copy | the same; the helper (§4) records its basis (`file_modification_time` or `stated_by_user`) |
| account details | — | **forbidden**: no user name, email, account id, plan id, cookie, token or URL query |

The other sections are unchanged: `publisher` (context only, checked against the catalog), `license`
(checked against the catalog, attribution required), and the credential scan.

**Provider registry** (`configs/archive_providers.json`), Transitland entry:

```json
"browser_acquisition": {
  "enabled": false,
  "record_url_pattern": "https://www.transit.land/data/feeds/{feed_id}/versions/{sha1}"
}
```

- `import_enabled` (API mode) stays `false`, and `download_hosts` stays empty.
- Browser mode is accepted only when `browser_acquisition.enabled` is true. Whether to set it to
  `true` is **Decision A** (§6).
- Record hosts stay `www.transit.land` and `transit.land`. No delivery or storage host is added
  anywhere.

## 3. Read side (History, API, inspector)

- **Classification.** `classify` keeps the kind `third_party_archived` and adds
  `acquisition_method` and `delivery_known: false`. The download origin is shown as *unknown*,
  never filled in.
- **Observation time:**
  - `observed_on` gives `observed_at = "2026-08-28"` as a date, with the basis
    `archive_record_date`;
  - sorting by observation time treats a date as that day;
  - the UI shows "observed 2026-08-28 (date only, per the archive record)".
- **Notice and label wording:** "archived copy from Transitland Feed Archive, downloaded through
  the user's browser; delivery address not recorded; originally published by ASPO Olbia; not
  downloaded from the publisher by this computer."
- **Unchanged:** the inspector never shows an archive URL as the official download (no change
  needed), and `feed_sources.download_url` is never touched (no change needed).

## 4. Record-writing helper (avoids hand-written JSON)

New `manage_snapshots.py archive-record`. It previews by default; `--write` creates the file.

```text
python scripts/manage_snapshots.py archive-record --zip <local zip> --source aspo \
    --provider transitland --feed-id f-spqb-aspoolbia --acquisition authenticated_browser \
    --observed-on 2026-08-28 [--retrieved-at <ISO time>] [--write]
```

What it does:
- **Computes:** SHA-1, SHA-256 and size; it refuses if the SHA-1 is not the version in the record
  URL it builds.
- **Fills in:** the publisher name and landing URL from the official catalog; the licence and an
  attribution text from the catalog and provider.
- **Sets `retrieved_at`:** by default, the ZIP's modification time in UTC (basis
  `file_modification_time`), or `--retrieved-at` (basis `stated_by_user`).
- **Validates** the result with the same `archive_problems` used by the import. Preview prints the
  record and any problems, and writes nothing.
- **With `--write`:** creates `<zip>.provenance.json` with an exclusive create, so it never
  overwrites. It never opens a database, never changes the ZIP, and never writes under `data/raw`.

## 5. Tests and verification (synthetic only)

**New tests**, on SQLite and disposable DuckDB:
- **Acceptance:** an honest browser-acquired record imports. It is attributed, non-activating, and
  leaves the selection and source URL unchanged. History and the API show "delivery unknown" and the
  date-only observation.
- **Refusals:**
  - a `download_url` or `final_download_url` present in browser mode;
  - a missing or other `delivery` value;
  - a record URL that differs from the pattern, or a feed id that doesn't match;
  - a SHA-1 mismatch;
  - a malformed `observed_on`, or one after retrieval, or both `observed_on` and `observed_at`;
  - browser mode on a provider without `browser_acquisition.enabled`;
  - account-like fields;
  - an official record in browser mode, and the reverse.
- **Regression:** all Phase 7C API-mode tests pass unchanged; the shipped-config test is updated for
  the new block; the schema digest is unchanged.
- **Helper:** computes the hashes, refuses a SHA-1 mismatch, previews without writing, writes with
  an exclusive create, refuses to overwrite, never opens a database and leaves the ZIP unchanged.
- **Race:** the ZIP or record replaced during staging or backup in browser mode is refused, or
  imports only the verified bytes.
- **Browser smoke:** a new demo stage `--admin-stage browser-archived` (invented provider) checks
  the labels, the date-only observation, sorting and a same-date comparison.

**Verification:**
- full pytest with services stopped;
- the synthetic History stages;
- `verify_local.ps1 -Browser` on the real database (read-only);
- the real database SHA-256 before and after;
- one local commit; no push.

## 6. Decisions needed from you

- **Decision A — enable Transitland's browser mode in the 7D1 commit?**
  - *Recommended: yes.* The code path is reviewed with the commit, and a real import still needs
    your separate approvals (§7).
  - Alternative: ship it disabled and flip it in a one-line reviewed change right before the import.
- **Decision B — source of `retrieved_at` for the real ZIP.**
  - *Recommended:* the file's modification time, 2026-11-07 11:21:10 UTC, recorded with basis
    `file_modification_time`.
  - Alternative: a time you state.

## 7. After 7D1: approvals for the real import (each separate)

1. Make an off-disk copy of the downloaded ZIP and `data/local-verification/phase7d0/` (you).
2. `archive-record` preview for the real ZIP, then `--write`. This creates one new file next to the
   ZIP in `data/downloads/…`; the ZIP itself is unchanged.
3. `manage_snapshots.py import --source aspo --zip <zip> --archive-provenance --real-database`
   **preview** (read-only), then review the plan.
4. The same command with `--apply`, with services stopped. It takes a verified backup and imports
   without activating.
5. Read-only checks: `aspo` stays selected at `2fdf3861…`, `feed_sources.download_url` is
   unchanged, the new snapshot is ready, and its archive and record hashes are verified. Then copy
   the new `data/raw/aspo/372e851c….zip` and its record off-disk.
6. The History same-date comparison on 2026-11-06 in the real UI, with attribution (CC BY 4.0, ASPO
   S.p.a. / Sardegna Mobilità, via the Transitland archive). It should reproduce Phase 7D0's
   isolated results.

**Separate follow-up (not 7D1):** a per-trip timetable diff in History. Phase 7D0 found two trip
changes that History's count-based summary cannot show.

## 8. Implementation and verification (2026-11-07)

**Code:**
- `archive_provenance.py`: `acquisition_method`, `_browser_problems`, the account-field check,
  `browser_record`, and read-side `acquisition_method`, `delivery_known` and `observed_on`;
- `admin.py`: `plan_archive_record` and `write_archive_record`;
- `history.py`: notice wording;
- `scripts/manage_snapshots.py`: the `archive-record` command;
- `configs/archive_providers.json`: the Transitland `browser_acquisition` block. `import_enabled`
  stays false and `download_hosts` stays empty;
- frontend: `api.ts` and `History.tsx` labels ("downloaded through the user's browser; delivery
  address not recorded", "date only").

**Unchanged:** the database schema (digest test passes); the official host allowlist;
`ingest.py` (archive records already never update `feed_sources.download_url` or activate).

**Python suite, services stopped:** **279 passed, 0 skipped** (251 earlier tests plus 28 new ones in
`backend/tests/test_phase7d1_browser_provenance.py`, run on SQLite and disposable DuckDB). The new
tests cover:
- helper preview and write, never overwriting, the ZIP unchanged, and refusal inside `data/raw`;
- attributed, non-activating import with the delivery address unknown and a date-only observation;
- 19 refusals: a download URL or final URL present, missing or other delivery value, record URL or
  feed id mismatch, invalid feed id, SHA-1 mismatch, bad, future or duplicate observation dates,
  missing or other retrieval basis, account id, e-mail, unknown method, credential key, publisher
  mismatch;
- modes kept separate;
- ZIP swap and record forgery during the backup;
- the shipped Transitland entry: browser-only, with an in-memory record for invented bytes passing
  against the real catalog, and API mode still refused;
- CLI preview and refusal.

**Mutation check:** switching off browser-mode validation fails 16 of the 28 new tests.

**Browser smoke** on a rebuilt production bundle, port 8765:
- the new `--admin-stage browser-archived` passes (labels, date-only observation, sorting,
  same-date comparison with notice);
- the `archived`, `preserved` and `activated` stages also pass.

**`verify_local.ps1 -Browser` on the real database (port 8765):** passed. Log:
`data/local-verification/phase7d1/verify-phase7d1-20261107-205647.log`.
- **pytest:** 273 passed, 6 skipped. The skips are the real-database read-only tests, which cannot
  open the file while the backend holds DuckDB's lock.
- **Startup:** backend startup 11.4 s, slower than the 4.7 s in Phase 7C and probably due to machine
  load; not investigated.
- **Browser checks:** the real four-source smoke, filter races, data-quality checks and the real
  History check all passed.

**Real database:** SHA-256 `3c308708…3ca0` and modification time 2026-11-06 17:33:51, unchanged
before and after every run. Services are stopped afterwards.

## 9. Real import and first genuine same-date comparison (2026-11-07, each step approved)

1. **Record preview** (`archive-record`, no write): passed with no problems.
2. **Record written** (`--write`): `data/downloads/archive/transitland/aspo/f-spqb-aspoolbia-b48388a2….zip.provenance.json`
   (SHA-256 `08fb5a07…e007`).
   - Content: version `b48388a2…`, `authenticated_browser`, delivery `unknown`, no download address,
     observed on 2026-08-28, retrieved 2026-11-07T11:21:10Z (file modification time).
   - The ZIP was unchanged.
3. **Import preview** (`import --archive-provenance --real-database`, no `--apply`): no problems.
   The selection would stay `2fdf3861…`.
4. **Import applied** (`--apply`, services stopped):
   - **Backup:** `data/backups/transport_atlas-20261107T122036599403Z-64c8d142/`, SHA-256 `3c308708…`
     (the pre-import database), checked with `verify-backup`.
   - **New snapshot** `396fbbacf74f0ad576e6d7919ec3092b`, ready: 17 routes, 423 stops, 496 trips,
     18,461 stop times.
   - **Archive:** `data/raw/aspo/372e851c….zip`, SHA-1 `b48388a2…` verified. The archived record's
     content is identical to the written one; its bytes differ (`27fcfd60…`) because the importer
     re-serialises the JSON.
   - **Database afterwards:** SHA-256 `1deb031a…e543ab`.
   - **Unrelated processes:** two short-lived `python.exe` processes were seen just before the
     import and had exited before they could be identified. DuckDB granted the import exclusive
     write access, so neither held the database.
5. **Checks after import (read-only):**
   - selection unchanged: arst `52a339e0…`, aspo `2fdf3861…`, atp_sassari `2f029bb3…`,
     trenitalia_sardegna `9208278…`;
   - the aspo `download_url` is still `https://www.aspo.it/wp-content/uploads/open_data.zip`;
   - 5 snapshots in total;
   - both ASPO archives verify by hash.

   The evidence manifest `data/local-verification/phase7d0/SHA256SUMS` lists all 7 files, and all
   check OK.
6. **History in the browser** (real database, production build, port 8765):
   - **Inventory:** ASPO lists 2 snapshots. The archived copy shows as "observed 2026-08-28, date
     only, per the archive record; obtained locally 2026-11-07 11:21:10", with the browser and
     delivery-unknown note and the CC BY 4.0 attribution. The current one shows as a publisher
     download.
   - **Comparison:** `same_date` on 2026-11-06, baseline `396fbbac` (archived), comparison
     `2fdf3861` (current).
     - unchanged: trips on the date 319 → 319, exact instances 319 → 319, routes scheduled
       15 → 15, stops served 421 → 421;
     - changed: route records 17 → 15, stop records 423 → 422, trip templates 496 → 394,
       shape IDs 79 → 68;
     - route records: `05PI` and `05PR` removed, `12` and `2S` renamed. Stop `1059` removed.
     - one notice: the baseline is an archived Transitland copy, downloaded through the user's
       browser, delivery not recorded, not downloaded from the publisher by this computer.
   - **Synthetic flag:** off.
   - **Console:** no errors.
   - **Back to atlas:** the selection is unchanged. With services stopped, the database is still
     `1deb031a…`.

**Interpretation:** see Phase 7D0 §5. Same service volume on 2026-11-06. The record removals concern
summer-only routes and an unserved stop, so they are not real-world closures. Two trip-level changes
(route 06 at 06:25, route 11 at 13:00 → 13:05) are not visible in History's count-based summary. The real
downloaded ZIP is unchanged (SHA-256 `372e851c…4715`), and no record exists next to it.
