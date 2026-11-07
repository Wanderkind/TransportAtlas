# Phase 7A — Safe snapshot administration

Status: implemented and verified on **synthetic, temporary and disposable
databases only**. Committed locally as `6407597` on `codex/local-real-feed-atlas` (based on
`e4db51a`); not pushed.

> **No second real feed version has been obtained or imported.** The real database
> (`data/transport_atlas.duckdb`) was opened read-only only; its selection and bytes
> are unchanged. Phase 6's historical comparison has still **not** been performed
> on real data. Doing so is Phase 7B and needs fresh authorization (see the end of
> this document).

## 1. Audit of the existing import path (before Phase 7A)

`ingest.import_file(con, zip, source_id=..., archive_dir=...)`:

1. Hashes the ZIP, then validates an optional `<zip>.provenance.json` sidecar
   (source and SHA-256 must match; the timestamp must carry a timezone).
2. If a **ready** snapshot with the same `(source_id, sha256)` exists, it returns
   `unchanged`. Duplicate bytes never create a second snapshot, and the selection
   is not touched.
3. It parses and validates the whole feed in memory before any write.
4. **Outside any transaction**, it copies the ZIP to
   `<archive_dir>/<source>/<sha256>.zip` (via a `.part` file plus an atomic
   rename) and writes the provenance sidecar next to it. An existing sidecar was
   overwritten unconditionally.
5. In **one transaction** it inserts the source row (if new), the snapshot and all
   rows, then **deletes and re-inserts `selection_members` for the source**. Every
   import therefore also activated the new snapshot.

Failure analysis:

| Situation | Database | Filesystem (before 7A) |
|---|---|---|
| Invalid ZIP, CRC or GTFS error | untouched (raised before the transaction) | untouched |
| Provenance mismatch | untouched | untouched |
| Duplicate bytes (ready snapshot) | untouched (`unchanged`) | untouched |
| Same bytes, non-ready row | unique-index error, rolled back | **new archive left unreferenced** |
| Error inside the transaction | rolled back | **new archive and sidecar left unreferenced** |
| Hard crash between archiving and COMMIT | not committed (verified with DuckDB) | archive left; harmless and reused by the next import of the same bytes |
| Existing archive with different bytes | refused | untouched |
| Existing sidecar with different content | — | **silently overwritten** |
| Concurrent process | DuckDB: an exclusive file lock across processes; SQLite: `BEGIN` without `IMMEDIATE` | — |

The main hazards for a *second* version were these: any import silently moved the
atlas to the new snapshot; failed imports could leave unreferenced archives; and
archived provenance could be rewritten.

Another hazard surfaced during implementation. **In DuckDB, `con.cursor()` opens a
separate connection**, so a read made through `storage.rows()` is *not* part of the
caller's open write transaction. Admin code that checked the precondition
through a cursor would have read committed state rather than the in-transaction
state. The first test run caught this with a read-back mismatch. `admin._rows`
therefore reads through the connection itself.

## 2. Design

**Explicit activation control** (the option the assignment preferred):
`import_file(..., activate=True)`.

- **The default `True` keeps the old behaviour by design.** It is used by the
  bootstrap importer `scripts/import_local_feeds.py` and by the Phase 6 fixtures.
  For a first import, selecting what you import is the intended outcome, and the
  existing evidence and tests depend on it. `test_legacy_import_still_activates_by_default`
  pins this.
- `activate=False` skips the two `selection_members` statements. Nothing else
  changes, so the selection is never touched; nothing is changed and then put
  back. This is safer than importing and then restoring the old pointer in a
  second step: there is no window in which the atlas serves the new snapshot, and
  no compensating write that could itself fail.
- Two importer hardening changes apply to all callers:
  - On a failed transaction, files *this call created* are removed, but only if
    no committed snapshot references these bytes. The exception carries a note
    saying what was removed (or left).
  - An existing archived provenance sidecar that differs is refused, never
    overwritten. An identical one is no longer rewritten.

The **administration layer** (`backend/transport_atlas/admin.py`, CLI
`scripts/manage_snapshots.py`) is never imported by the web app; the atlas and
History endpoints stay GET-only and read-only. Selection changes swap the pointer
in one small transaction. They never copy or re-import a ZIP.

### Invariants and where they are enforced

| Invariant | Enforcement | Test |
|---|---|---|
| Snapshots stay immutable and source-scoped; old ones are retained | No admin command deletes or updates `feed_snapshots` | all state comparisons |
| Same bytes and source → no duplicate | `plan_import` → `action: existing`, no backup, no write | `test_duplicate_bytes_import_is_idempotent_and_keeps_selection` |
| Preserve-mode import never changes the selection | `activate=False`, plus a before/after state assertion inside the guarded write | `test_import_v2_preserves_v1_selection…`, `test_import_file_activate_false…` |
| A new source stays unselected unless activated | same | `test_valid_provenance_new_source_is_archived_but_not_added_to_the_atlas` |
| Only a ready snapshot of the same source, with a present and hash-verified archive | `_target_problems`, checked in the preview *and* inside the transaction | `test_wrong_source_missing_nonready_and_broken_archive…` |
| Expected-current precondition; abort on mismatch | `change_selection` reads the current selection inside `BEGIN IMMEDIATE` (SQLite) or `BEGIN TRANSACTION` (DuckDB) on the same connection | `test_stale_expected_current_fails_with_no_change` |
| No half-committed import; orphans removed or reported | importer rollback and cleanup; `status` lists unreferenced archives and `.part` files | `test_importer_failure_rolls_back…`, `test_hard_crash_mid_import…`, `test_partial_files_are_reported_not_used` |
| Every operation names its database and source | banner with absolute path, backend, size, mtime and real-DB flag; plans echo `source_id` | CLI test |
| A misspelled path never creates a database | `resolve_database` requires an existing file; inspection opens read-only and never runs schema DDL | `test_cli_preview_by_default_and_explicit_apply` |
| A verified backup exists before any write | `guarded()`: stopped-services check → backup → open for write → state must equal the backup's | `test_failed_backup_prevents_mutation`, lock tests |
| The read endpoints never mutate selection | unchanged Phase 6 tests, plus the API comparisons here | `test_activate_then_restore_round_trip_and_atlas_follows` |

## 3. Commands

All commands **preview by default** and exit 2 if the change would be refused.
Only `--apply` writes. There is no interactive prompt, so the tool is
deterministic in tests. The required `--expected-current` and the
`--real-database` flag are the explicit confirmations.

```powershell
# Inspect (read-only; works only while the backend is stopped, because DuckDB locks the file)
.\.venv\Scripts\python.exe scripts/manage_snapshots.py status --db data/transport_atlas.duckdb --verify-archives
.\.venv\Scripts\python.exe scripts/manage_snapshots.py status --db <db> --source <id> --json

# Import an existing local ZIP; the selection is always preserved
.\.venv\Scripts\python.exe scripts/manage_snapshots.py import --db <db> --source <id> --zip <file.zip>           # preview
.\.venv\Scripts\python.exe scripts/manage_snapshots.py import --db <db> --source <id> --zip <file.zip> --apply

# Select a ready snapshot (or `none`), only if the current one is as expected
.\.venv\Scripts\python.exe scripts/manage_snapshots.py activate --db <db> --source <id> --snapshot <new-id> --expected-current <old-id|none>
#   ... --apply

# Undo the most recent journaled selection change of a source
.\.venv\Scripts\python.exe scripts/manage_snapshots.py restore --db <db> --source <id>            # preview
#   ... --apply

# Backups
.\.venv\Scripts\python.exe scripts/manage_snapshots.py verify-backup  --metadata data/backups/<run>/backup.json
.\.venv\Scripts\python.exe scripts/manage_snapshots.py restore-backup --metadata data/backups/<run>/backup.json --to <new path>  # preview; add --apply
```

Common options: `--backend duckdb|sqlite` (otherwise taken from the suffix:
`.duckdb`, `.sqlite`, `.sqlite3`, `.db`), `--backup-dir` (default `data/backups`),
`--json`. Snapshot IDs must be the full 32 hex characters; prefixes are refused.

### `import` checks

- The ZIP is an existing local file; there is no URL or download path.
- SHA-256, ZIP CRC (`testzip`), and a full parse plus the importer's validation.
- **Real feeds need a provenance sidecar**, checked against the official catalog
  (`configs/sardinia_sources.json`) with the same rules as the Phase 3/4 audit:
  - source ID, SHA-256 and byte count match;
  - the retrieval timestamp carries a timezone;
  - `download_url` passes `official.safe_url` (HTTPS, approved hosts, no
    credentials or port);
  - `landing_url` equals the catalog.
- `--synthetic` is only for invented fixtures. It is refused for official catalog
  sources, for a non-synthetic existing source, and when a sidecar is present. A
  new synthetic source gets the publisher `SYNTHETIC fixture (not a real operator)`.
- A source not in the database needs `--new-source`; a typo is refused. A new
  non-catalog source without `--synthetic` is refused.
- The archive root is inferred from existing snapshots (`data\raw` for the real
  database) or given with `--archive-dir`. A different-bytes archive, a
  differing archived sidecar, or a non-ready row with the same bytes is refused.
- The preview shows: new vs existing snapshot, the snapshot ID it will get,
  archive state (create, or reuse identical bytes), counts, and
  `selection: preserved`.

## 4. Backup, journal and rollback

**Backup method** (`admin.create_backup`, run automatically before every `--apply`):

1. Refuse if `local-services.json` records running services, or if an
   uncheckpointed `.wal` (DuckDB) or `-wal`/`-journal` (SQLite) file exists.
2. Take a lock that **excludes writers** for the whole copy. For DuckDB that is a
   read-only connection: verified on this machine, another process can then
   neither open the file for writing nor (while a writer holds it) even read it.
   For SQLite it is `BEGIN EXCLUSIVE`.
3. Copy into a new, uniquely named folder
   `data/backups/<db-stem>-<UTC timestamp>-<random>/` as a `.part` file, then fsync.
4. Require source SHA-256 before = after = copy SHA-256. Rename the `.part`.
5. Open the backup read-only (SQLite also runs `PRAGMA integrity_check`). Require
   its selection, snapshot and source rows to equal the source's.
6. Write `backup.json` in exclusive-create mode. It records the source path,
   backup path, bytes, SHA-256, source mtime, method, checks, logical state and
   git commit.

Any failure removes only the files this run created and raises
`Backup failed; no change was made`. The database is then opened for writing,
and the mutation **aborts if the logical state differs from the backup's**. This
covers the short gap between releasing the backup lock and taking the write lock.
Backups never overwrite: each run gets a new folder, and the restore destination
must not exist.

**Selection journal**: `data/backups/selection-journal.jsonl` (append-only,
fsynced). Each `activate` or `restore` writes an `intent` line before the
transaction and an `applied` or `aborted` line after it, with the absolute
database path, source, before, after and operation ID. `restore` undoes the most
recent `applied` change of a source. It still requires the current selection to
equal that change's `after`, so it never auto-reverts over a newer change. It
refuses if the journal shows an `intent` without an outcome (inspect `status`,
then use `activate` explicitly). Running `restore` twice re-applies the original
change, because each restore is itself a journaled change.

**Restoring a whole database** (manual by design; never done automatically, and
not done during development):

```powershell
.\scripts\stop_local.ps1
.\.venv\Scripts\python.exe scripts/manage_snapshots.py verify-backup  --metadata data\backups\<run>\backup.json
.\.venv\Scripts\python.exe scripts/manage_snapshots.py restore-backup --metadata data\backups\<run>\backup.json --to data\restore\transport_atlas.duckdb --apply
# Inspect the restored copy, then swap files by hand, keeping the current one:
Move-Item data\transport_atlas.duckdb data\transport_atlas.before-restore.duckdb
Move-Item data\restore\transport_atlas.duckdb data\transport_atlas.duckdb
(Get-FileHash data\transport_atlas.duckdb -Algorithm SHA256).Hash   # must equal backup.json sha256
```

Archive files under `data/raw` are content-addressed and never deleted by these
tools. After a database restore, any archive imported later is simply
unreferenced: `status` lists it, and a re-import reuses it.

**Limitations**:
- Backups are full copies (about 67 MB for the real database), and nothing prunes
  them.
- The services check relies on `local-services.json`. A backend started by other
  means is still caught by the DuckDB lock, but not by the record.
- The journal lives beside the backups. Deleting `data/backups` loses the
  ability to `restore`, though `activate` with explicit IDs still works.
- SQLite crash recovery (a hot `-journal`) needs one normal read-write open
  before the admin tool will back it up.

## 5. Verification

### Python tests

- `backend/tests/test_phase7a_admin.py`: **43 tests**, all passing. Most state
  transitions are parametrized over SQLite *and* a disposable DuckDB file under
  pytest's temporary directory, using the real admin functions and CLI rather
  than mocks. They cover:
  - legacy activation by default, and `activate=False`;
  - refusal to overwrite differing archived provenance;
  - import-preserve, then History listing both versions and comparing them;
  - activate, then restore, with the atlas API following each change and
    returning to identical `/snapshots` and `/summary` output;
  - restore refused after an out-of-band change;
  - stale expected-current (preview and in-transaction);
  - wrong source, missing, failed (non-ready), archive missing and archive
    tampered;
  - malformed IDs and unknown sources;
  - duplicate-bytes idempotence;
  - junk ZIP, CRC-corrupt ZIP, invalid GTFS, missing ZIP, missing sidecar;
  - seven provenance mismatches (hash, source, bytes, naive timestamp, `http`,
    foreign host, landing URL), plus a valid new provenanced source that stays
    out of the atlas until activated;
  - injected importer failure mid-transaction, which rolls back and removes the
    new archive;
  - failing and corrupting backup copies, which block the mutation and leave no
    backup or journal behind;
  - the services record present;
  - an uncheckpointed WAL or journal;
  - a backup that is hash-equal to the source, verifiable, restorable to a new
    path with identical bytes and state, never overwritten, and caught when
    tampered;
  - a DuckDB lock held by another process and a SQLite `BEGIN EXCLUSIVE` lock,
    both refused with no backup made;
  - a hard crash (`os._exit`) of a DuckDB import between archiving and COMMIT:
    the database is unchanged, `status` reports the unreferenced archive, and a
    re-import reuses it;
  - stray `.part` files reported and never touched;
  - CLI preview-by-default, `--apply`, refused previews returning exit 2, a typo'd
    path not created, and the real-database flag;
  - a read-only real-database preflight.
- **Failing before the fix**: with the old `ingest.py` temporarily restored,
  8 of the 9 importer-dependent tests fail. The ninth is the legacy-default test,
  which must pass on both. The DuckDB same-connection defect was caught by 5
  failing tests on the first run.
- Full suite with services **stopped**, run from the repository root as
  `verify_local.ps1` does: **187 passed, 0 skipped** (the previous 144 plus 43).

### Full local verification (`verify_local.ps1 -Browser`, services running on the real database)

Log: `data/local-verification/phase7a/verify-phase7a-20261107-161550.log`. Services
were started with `start_local.ps1 -Production -ApiPort 8765` (see the port note
below). The run **passed**:

| Suite | Result |
|---|---|
| pytest (services running) | **181 passed, 6 skipped**. The skips are the expected real-DuckDB cases (5 from Phase 6, plus the new read-only preflight), which cannot open the file while the backend holds its lock |
| Backend startup probe | passed, loopback, 6.1 s |
| TypeScript and Vite production build | passed |
| Real-data smoke (4 sources) | passed, 6 check groups; initial map ready in 15.0 s |
| Delayed, error and filter-race checks | passed, 4 groups; initial 17.5 s, old map hidden 0.74 s after a date change, new date ready 15.4 s |
| Data-quality indicator checks | passed, 5 groups |
| History view (real mode) | passed: each operator shows its single snapshot, comparison is explained as impossible, and the atlas is unchanged (349 routes) |

The whole run took 24.7 minutes. Map-ready times were about twice Phase 6's
(7.2 s initial). Phase 7A changed no map, network or cache code, and the run
shared the machine with the test suite and browser. This is recorded as an
observation, not a measured regression.

A first attempt of this run failed one case: the new real-database preflight
hashed the file before checking the lock, and Windows denies reads while the
backend holds DuckDB's write lock. The test now skips in that situation like
the other real-database cases. The fix was confirmed by the rerun above and by
the final run with services stopped (**187 passed, 0 skipped**).

### Synthetic browser checks (`smoke_history_ui.py`)

`build_history_demo.py --admin-stage` builds the disposable demo database through
the admin path, with backups under `data/history-demo/backups/`:

| Stage | How v2 arrived | Current | Result |
|---|---|---|---|
| `preserved` | `apply_import` (synthetic, selection preserved) | v1 | **passed** (`phase6-history-synthetic-v1.json`): both versions listed, the current-atlas flag on v1, full comparison workflow |
| `activated` | then `apply_selection(v2, expected v1)` | v2 | **passed** (`phase6-history-synthetic-v2.json`) |

The smoke now derives the expected "current atlas" flag from `/api/v1/snapshots`
instead of assuming v2, so it checks either stage. The default `legacy` stage
behaves exactly as in Phase 6.

### Real database: read-only preflight and post-check

| | Before work | After work |
|---|---|---|
| SHA-256 | `3c308708af91052c06a5ed4479ec6b0d5332a71c056a9d25d8215da322523ca0` | `3c308708af91052c06a5ed4479ec6b0d5332a71c056a9d25d8215da322523ca0` (identical; mtime still 2026-11-06 17:33) |
| Size | 66,596,864 bytes | same |
| Selection | arst `52a339e0…`, aspo `2fdf3861…`, atp_sassari `2f029bb3…`, trenitalia_sardegna `92082781…` | identical (4 rows, 4 snapshots); no backup of it was taken because no mutation targeted it |

The preflight found:
- four sources, each with exactly one `ready` snapshot that is selected;
- every archive present and hash-verified, each with a provenance sidecar.

`status` also reports one pre-existing unreferenced archive,
`data/raw/synthetic_smoke/8ffa…zip`, from an earlier synthetic smoke run against
another database. It is left untouched as earlier evidence. The pre-state record
is `data/local-verification/phase7a/real-db-pre.json`.

### Environment note: port 8000 reserved by Windows

During this phase Windows reserved TCP ports 7940–8039 (`netsh interface ipv4 show
excludedportrange protocol=tcp`), so the backend could no longer bind 8000
(WinError 10013). System network settings were not changed. Instead,
`start_local.ps1` gained `-ApiPort` (default 8000, still bound to 127.0.0.1), which
sets `TRANSPORT_ATLAS_API_PORT`. The Vite dev/preview proxy, `browser_checks.py`
and `profile_local.py` read the same variable. All browser runs in this phase used
`-ApiPort 8765`.

## 6. Remaining risks

| Severity | Issue |
|---|---|
| Medium | The bootstrap importer still activates by design. Running `import_local_feeds.py` with a *new* ZIP in `data/downloads/` would select it. Use `manage_snapshots.py import` for later versions; this is documented in the README. |
| Low | History's `archive.present` and `provenance.declared_info` resolve the real database's relative `data\raw\…` paths against the process working directory. The backend and `verify_local.ps1` run from the repository root, but pytest started from `backend/` fails the Phase 6 real-database test. (Pre-existing; the admin module resolves against the repository root.) |
| Low | If a crash leaves an unreferenced archive and its sidecar, and the same bytes are later re-downloaded (new `retrieved_at`), the import is refused because the archived sidecar differs. This is safe but needs a manual decision (move the orphan aside) and is never automatic. |
| Low | Backups are not pruned, and the services check depends on the service record (the DuckDB lock remains the hard guard). |
| Low | An `apply_import` that fails after the backup leaves that backup in place. This is intended, and the backup is listed in the error context. |
| Info | The DuckDB `cursor()` behaviour means other code that mixes `storage.rows()` reads with an open write transaction would read committed state. `import_file`'s only such read is before `BEGIN`. |

## 7. Phase 7B research (no data fetched)

On 2026-11-07, read-only, the official landing page
`https://www.sardegnamobilita.it/open-data/quadri-orari-trenitalia` lists:
- one resource, `https://www.sardegnamobilita.it/opendata/R_SARDEGTRASP_00008_1_dati_trenitalia.zip`;
- "Ultima modifica 2026/06/09";
- validity 2026-06-06 to 2026-12-12;
- update frequency "IRREG";
- **no archive of other versions and no licence text** (only the regional
  footer copyright).

The imported snapshot (`b27657fb…`, coverage 2026-06-06..2026-12-12, feed version
"GTFS passa per Sardegna_20260605_092435") is therefore the current published file.

Implications:
- A second version will most likely appear only when the publisher **replaces the
  same URL**, presumably around the 2026-12-13 timetable change. It must be
  recognised as genuinely new by its different SHA-256 and `feed_info`.
- If the new calendar starts on 2026-12-13, it will **not overlap** the current
  one. A `same_date` comparison would then be impossible, leaving only the
  explicitly labelled `different_dates` policy. Overlap must be checked before
  promising a same-date comparison.
- Licensing and redistribution terms remain unasserted (as for all four feeds).

No ZIP was downloaded, and no second version is claimed to exist.

## 8. Phase 7B authorization checklist (not performed)

Each item needs **fresh, explicit user authorization**:

1. **Source and feed**: which operator (likely `trenitalia_sardegna`), and
   acceptance that the candidate is the official URL above. Acceptance that it is
   genuinely new only if its SHA-256 differs from `b27657fb…`.
2. **Download**: running `python -m transport_atlas fetch-official --source …
   --output <new path>`. This enforces HTTPS and approved hosts and writes a
   provenance sidecar. Use a *new* path, not `data/downloads/<source>.zip`, so the
   earlier evidence and its sidecar are preserved.
3. **Validation**: a MobilityData validator run (Phase 4 procedure) on the new
   ZIP, plus an `import` preview (CRC, parse, provenance, new-vs-existing, overlap
   from the coverage dates).
4. **Backup**: accepting the automatic verified backup of `data/transport_atlas.duckdb`
   (about 67 MB) under `data/backups/`, with services stopped.
5. **Import**: `manage_snapshots.py import --db data/transport_atlas.duckdb
   --source … --zip … --apply --real-database`. The selection is preserved.
6. **Selection disposition**: whether to keep the current snapshot selected, or
   `activate` the new one with `--expected-current 9208278118a446e008dcd4f96da8bf11`,
   and whether and when to `restore`.
7. **Real comparison**: running and reporting a History comparison between the
   two real versions. If the calendars do not overlap, decide whether a
   `different_dates` comparison is acceptable.
8. **Push or merge** of any resulting commits.

## 9. Files

- New: `backend/transport_atlas/admin.py`, `scripts/manage_snapshots.py`,
  `backend/tests/test_phase7a_admin.py`, this document.
- Changed: `backend/transport_atlas/ingest.py` (`activate`, failure cleanup,
  sidecar protection), `scripts/build_history_demo.py` (`--admin-stage`),
  `scripts/smoke_history_ui.py` (current flag from the API),
  `scripts/start_local.ps1`, `frontend/vite.config.ts`, `scripts/browser_checks.py`,
  `scripts/profile_local.py` (API port override), `README.md`,
  `docs/IMPLEMENTATION_STATUS.md`.
- Untouched and unstaged: `PHASE4_APPLY.md`, `codex-phase3f-review.patch`.
