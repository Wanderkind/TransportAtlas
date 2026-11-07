# Phase 7D2 — Post-import evidence bundle and provenance-writing fixes

**Date:** 2026-11-07. Local commit only: no push, PR or CI.
**Baseline:** `codex/local-real-feed-atlas` at `87c6864e2f7a2e9b83069190ca4ccf125dd317c3`, equal to origin.
The worktree was clean apart from the two untracked root files, which were left untouched.

**Authorization.** The approvals recorded in [PHASE7D1_PLAN.md](PHASE7D1_PLAN.md) §9 are historical
facts. Phase 7D2 made **no** real-database mutation, re-import, activation or restore.

## A. Post-import evidence

### Baseline before any work

Services were stopped, and no managed or Python writer was running.

| | value |
|---|---|
| Real DB | `data/transport_atlas.duckdb`, SHA-256 `1deb031a169675a05cc9124498b291c6252dc4f0213f96593a7d80b052e543ab`, 66,334,720 bytes, modified 2026-11-07T12:20:53Z, no WAL file |
| Selection | arst `52a339e0…`, aspo `2fdf3861…`, atp_sassari `2f029bb3…`, trenitalia_sardegna `9208278…` |
| Snapshots | 5, all `ready`, archives present and SHA-256-verified, all with sidecars. ASPO `396fbbac…` is `third_party_archived` and not selected |
| Official URLs | unchanged; aspo is `https://www.aspo.it/wp-content/uploads/open_data.zip` |
| Pre-import backup | `data/backups/transport_atlas-20261107T122036599403Z-64c8d142/`, `verify-backup` reports verified, SHA-256 `3c308708…3ca0` |
| Unreferenced archive | `data/raw/synthetic_smoke/8ffa967d….zip` (left alone) |

The read-only `status --verify-archives` output is in `data/local-verification/phase7d2/status-before.json`.

### Recovery bundle

**Location:** `data/backups/phase7d2-post-import-evidence-20261107T130434Z/`. It is git-ignored,
and it was created in a new folder so nothing existing could be overwritten.

**Tool:** `scripts/build_evidence_bundle.py` (new and committed). It only reads the originals. It
writes only into the new bundle folder, plus a restore test under `data/local-verification/phase7d2/`.

| part | content |
|---|---|
| `database/<folder>/` | **Post-import** database: a consistent copy made with `admin.create_backup`, which holds a read-only DuckDB connection (excluding writers), checks the source bytes before and after, refuses if WAL files exist, and compares the logical state. SHA-256 `1deb031a…` |
| `pre-import-database/` | Byte copy of the verified **pre-import** backup (`3c308708…`) and its `backup.json` |
| `archives/<source>/` | **All 5** ZIPs referenced by `feed_snapshots` and their 5 provenance sidecars (arst, aspo ×2, atp_sassari, trenitalia_sardegna) |
| `downloads/` | The user-downloaded historical ASPO ZIP (SHA-1 `b48388a2…`, SHA-256 `372e851c…`) and its record |
| `evidence-phase7d0/` | Phase 7D0 inventory, checksum lists, GTFS summaries, the isolated comparison, and validator `report.json` / `system_errors.json` |
| `manifest.json` | Each file's role, original path, source and snapshot IDs, bytes and SHA-256, plus the real-database state, the checks and the restore test |
| `SHA256SUMS`, `SHA1SUMS`, `README-RESTORE.md` | Checksums, the historical ZIP's SHA-1, and restore instructions saying which copy is pre-import and which is post-import |

**Result:**
- **Size:** 28 files, 156,351,896 bytes.
- **Checksums:** an independent `sha256sum -c SHA256SUMS` gives 28 × OK, and `sha1sum -c SHA1SUMS` gives OK.
- **Originals unchanged:** after bundling, the real database is still `1deb031a…`, with the same logical state and selection.
- **Restore test:** the bundled post-import copy was restored to
  `data/local-verification/phase7d2/restore-test-20261107T130434Z/transport_atlas.duckdb` with the
  tested `restore-backup` routine. Its bytes equal the bundle copy, and its logical state equals the
  live database. Nothing was swapped into the live path.

**Note on restoring.** `backup.json` records absolute paths, so once the folder is moved,
`README-RESTORE.md` uses a hash-checked manual copy to a new path.

### Off-disk copy (still needs your action)

Nothing has been copied off this disk. Replace `E:\TransportAtlasBackup` with your external drive,
then run in PowerShell from the repository root:

```powershell
$src = 'data\backups\phase7d2-post-import-evidence-20261107T130434Z'
$dst = 'E:\TransportAtlasBackup\phase7d2-post-import-evidence-20261107T130434Z'
robocopy $src $dst /E /COPY:DAT /DCOPY:T /R:1 /W:1
Push-Location $dst
Get-Content SHA256SUMS | ForEach-Object { $h,$p = $_ -split '  ',2; if ((Get-FileHash -Algorithm SHA256 $p).Hash.ToLower() -ne $h) { "MISMATCH $p" } }
Pop-Location
```

No output from the last loop means every copied file matches. Keep the bundle out of Git.

## B. Provenance-writing fixes

### B1. Independent expected-version anchor

**The problem.** Before this phase, `archive-record` built the version URL from the SHA-1 it computed
itself, and validation compared that same SHA-1 with that URL. The check was circular.

**The fix.** A record can now only be written if its archive version comes from outside the tool.
- `manage_snapshots.py archive-record` requires **`--expected-sha1 <40-hex>`**, the version ID read
  from the provider's public version page. It can also take `--expected-record-url`, which must be
  exactly the canonical page for that SHA-1.
- `admin.plan_archive_record`:
  - refuses a missing or malformed value (not exactly 40 lowercase hexadecimal characters);
  - reports a mismatch with the ZIP's computed SHA-1, and never adopts the computed value as the claim;
  - re-hashes the ZIP after building the record.
- New records carry `archive.version_anchor`: the expected version, the record URL, the method, and
  `remote_page_reverified_offline: false`. The remote page is never presented as re-checked.
- **Compatibility:** `archive_problems` does not require `version_anchor`. The record already written
  in Phase 7D1 and the imported ASPO snapshot `396fbbac…` stay valid and untouched; a read-only test
  checks this.

### B2. Atomic, no-clobber record creation

**The problem.** Before this phase, the record was written directly to its final path with
`open('x')`. A failure part-way could leave a partial file.

**The fix.** `admin.write_archive_record` now:
1. writes the JSON to a unique `<record>.<uuid>.part` sibling, then flushes and fsyncs it;
2. checks the temporary file's bytes and re-hashes the ZIP; if the ZIP changed, nothing is published;
3. publishes with `os.link(temporary, final)`. Creating a hard link is atomic and fails if the
   destination exists on NTFS and POSIX filesystems. `Path.replace()` is not used, because it can
   overwrite;
4. **fails closed** on filesystems without hard links (e.g. FAT32/exFAT), saying so, rather than
   falling back to a rename that could overwrite;
5. if the ZIP changes right after publication, removes the final record **only if it is the file this
   call published** (`os.path.samefile`). It never removes a record another process created;
6. always removes the temporary name.

### Tests

`backend/tests/test_phase7d2_record_hardening.py`: 20 cases.
- **B1:**
  - a missing expected SHA-1;
  - 7 malformed values;
  - a mismatch, leaving the ZIP unchanged and no record;
  - a record URL that is not canonical (4 variants), and the canonical one accepted;
  - an honest record with its anchor;
  - a read-only regression against the real download (`b48388a2…` matches, a wrong value is refused,
    the Phase 7D1 record is still valid, and the folder hashes and modification time are unchanged).
- **B2:**
  - disk full while writing;
  - a serialization error;
  - an existing destination;
  - a destination created concurrently (kept);
  - the ZIP changed before publication;
  - the ZIP changed just after publication (only our record removed);
  - no hard-link support (fails closed);
  - success with exact bytes and no `.part` left over.

**Mutation check:** publishing with `os.replace` instead fails 2 of these tests.

The Phase 7D1 tests and the demo builder now pass `expected_sha1`.

## Real-database test invariants updated

Two read-only tests assumed the pre-import real state of exactly one snapshot per source:
`test_phase6_history::test_real_database_has_one_version_per_source_and_stays_unchanged` and
`test_phase7a_admin::test_real_database_preflight_is_read_only`. This was the first services-stopped
run since the approved import, so they failed. They now assert the invariants that must hold:
- exactly one selected, publisher-direct snapshot per source, validated with 0 errors;
- every other snapshot is ready, unselected, `third_party_archived` and hash-verified;
- comparison is possible only for sources with two ready snapshots.

The first test was renamed `test_real_database_history_invariants`.

## Verification

- **Python, services stopped:** 313 passed, 0 skipped. Real database `1deb031a…` before and after.
- **`verify_local.ps1 -Browser` on the real database:** passed (307 passed and 6 skipped under the
  running backend's lock; browser checks passed). See
  [PHASE8A_TIMETABLE_DIFF.md](PHASE8A_TIMETABLE_DIFF.md) §Verification.
- **Real database afterwards:** SHA-256 `1deb031a…`, modification time 2026-11-07T12:20:53Z, the
  selection and official URLs unchanged. The historical download and its record are unchanged
  (`372e851c…` / `08fb5a07…`).
