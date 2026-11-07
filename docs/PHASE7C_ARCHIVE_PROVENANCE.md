# Phase 7C — Honest provenance for archived GTFS copies

**Status (2026-11-07):** implemented and tested on synthetic data only, local commit, not pushed.
**Baseline:** `c5cae7c` (Phase 7B report) on `codex/local-real-feed-atlas`. That commit was
already on origin, so the starting point was clean.

Phase 7C lets a GTFS ZIP that was *legitimately obtained from a third-party archive* be imported
later with honest attribution. It does not enable that import for any real archive yet.

What was not done:
- **No feed was downloaded.** No archive was accessed, no account or credential was used, and the
  older ASPO version `b48388a26eeffbadde634b526dc3cb1acd8606ab` was not obtained, validated or
  imported.
- **The real database was not changed.** `data/transport_atlas.duckdb` had SHA-256
  `3c308708af91052c06a5ed4479ec6b0d5332a71c056a9d25d8215da322523ca0` and modification time
  2026-11-06 17:33:51 +09:00, unchanged after every test and browser run (§9). Its selection is
  unchanged.
- **Every example is invented.** They use the source `synthetic_official`, publisher
  "SYNTHETIC official-style source", the feed "SYNTHETIC Valley Transit" and the provider
  "SYNTHETIC Example Archive" on the reserved `.test` TLD. None of it is Sardinian history.

---

## 1. Architecture audit (before the change)

| question | finding (at `c5cae7c`) |
|---|---|
| Where provenance lives | In a JSON sidecar next to each content-addressed archive, `data/raw/<source>/<sha256>.zip.provenance.json`, copied from the download's own sidecar by `ingest.import_file`. The import refuses to overwrite a differing archived sidecar (Phase 7A), so the record is already **per snapshot and immutable**. The database holds only `feed_snapshots.retrieved_at` (copied from the sidecar) and `feed_sources.download_url`. |
| How `feed_sources.download_url` changes | `import_file` ran `UPDATE feed_sources SET download_url=<sidecar download_url>` on **every** import that had a sidecar. The column is stored once per source, so the last import wins. |
| Immutable snapshot identity | `snapshot_id = sha256(source_id + ':' + content_sha256)[:32]`, with a unique index on `(source_id, content_sha256)`. Archive path `<raw>/<source>/<sha256>.zip`. |
| How the API renders dates | History `inventory` sorted by `retrieved_at` and labelled it "when this local copy was downloaded". `feed_info` declared validity and observed calendar coverage are separate fields. The route inspector (`route_details.route_evidence`) read `download_url` and `landing_url` straight from the sidecar. The Quality panel shows `retrieved_at` as "Local copy retrieved". |
| Code paths that open the real DB, and where DDL runs | `api.create_app` (app startup), `__main__` and `scripts/import_local_feeds.py` call `storage.connect()`. It opens **read-write** and runs `SCHEMA`, which is all `CREATE TABLE/INDEX IF NOT EXISTS`. `scripts/build_history_demo.py` refuses any folder except `data/history-demo/`. `admin.open_readonly`/`open_write` never run DDL, and `audit_local_evidence.py` and `validate_external_gtfs.py` open read-only. **Any table or column added to `SCHEMA` would therefore be created in the real database by the next app start.** |

## 2. Design decision: no schema change

The provenance stays in the immutable per-snapshot sidecar. That location already has the
properties needed: it is per snapshot, content-addressed, and refuses to be overwritten.
`storage.SCHEMA` is **byte-identical** to `b9894482`; its digest
`76ad985c…84ce` is frozen in a test. So:
- startup DDL on the real database stays a no-op, exactly as before;
- no migration exists, so none can run silently.

Trade-offs:
- **For:** no migration, no backfill, nothing to roll back. Provenance cannot be overwritten by
  another import of the same source, because each snapshot has its own file at its own
  content-addressed path. Legacy snapshots need no change.
- **Against:** the evidence sits on the filesystem beside the archive, not inside the database
  transaction.
  - A crash between writing the sidecar and the commit can leave an *unreferenced* archive and
    sidecar. They are harmless: `manage_snapshots.py status` lists them, and the next import of
    the same bytes reuses them only if the record is identical.
  - Phase 7A backups copy the database, not `data/raw`.
  - A sidecar can be edited by hand after import. Readers check that its `source_id` and
    `sha256` match the snapshot; otherwise they show it as unknown, with the problem stated.
- **Consistency guarantees:**
  - Validation and the import read the same private, staged bytes (Phase 7A).
  - The archived sidecar is written through `.part` + rename and refused if it differs.
  - The snapshot row and the sidecar refer to the same SHA-256.
  - The database transaction rolls back and removes only files this import created.
- **Recovery:**
  - **Database:** restore the verified backup that the guarded import took (`verify-backup`,
    then `restore-backup` to a new path).
  - **Provenance:** the sidecar is regenerable only from the original evidence, so keep a copy of
    `data/raw/<source>/` with the downloaded ZIP and its record.
- **If a database-level record is ever required:** that is a separate migration phase. Add a
  `snapshot_provenance` table through an explicitly gated, backed-up migration command, never
  through `connect()`. Test it on copies first.

## 3. Evidence classes

`archive_provenance.classify(snapshot)` describes a snapshot without inventing facts:

| kind | meaning | download origin shown | observation time |
|---|---|---|---|
| `publisher_direct` | The sidecar has a top-level `download_url`: this computer downloaded the bytes (the official downloader's record) | that URL | the local retrieval time (`local_retrieval`) |
| `third_party_archived` | An archive record (§4) | the archive's HTTPS URL; **never** shown as the official download | `archive.observed_at` (`archive_record`), or unknown |
| `synthetic` | An invented fixture with no record | none | unknown |
| `unknown_legacy` | No usable record, or one that is unreadable or does not match the snapshot's source or SHA-256 (`problem` explains which) | none | unknown |

`synthetic` is also reported as a separate boolean, so an invented archived fixture is shown as
"Archived copy" **and** "synthetic".

## 4. The archive provenance record (`transport-atlas/third-party-archive-provenance/1`)

The record is written next to the ZIP as `<zip>.provenance.json`, then archived as
`<raw>/<source>/<sha256>.zip.provenance.json`. It **must not** contain a top-level
`download_url` or `landing_url`. Without those keys, no older reader can mistake it for an official
download.

```json
{
  "schema": "transport-atlas/third-party-archive-provenance/1",
  "provenance_kind": "third_party_archived",
  "source_id": "<catalog source id>",
  "sha256": "<SHA-256 of the exact local ZIP, computed here>",
  "bytes": 123456,
  "retrieved_at": "<when THIS computer obtained the copy, timezone-aware>",
  "publisher": {
    "name": "<must equal the catalog publisher>",
    "landing_url": "<must equal the catalog landing URL: context, not the download origin>",
    "reported_origin_url": "<optional: the publisher URL the archive says it fetched from; must pass the official HTTPS allowlist>"
  },
  "archive": {
    "provider": "<key in configs/archive_providers.json>",
    "provider_name": "<must equal the registered name>",
    "record_url": "<HTTPS page for this version on the provider's record host; must contain version_id>",
    "version_id": "<the archive's 40-hex SHA-1, checked against the SHA-1 of these bytes>",
    "download_url": "<HTTPS URL actually used, on the provider's download hosts; no query or fragment>",
    "final_download_url": "<optional: final URL after redirects; same host rule>",
    "observed_at": "<optional: when the archive fetched it from the publisher, timezone-aware, not after retrieved_at>",
    "observed_at_evidence_url": "<required with observed_at: HTTPS page on a record host where it was read>"
  },
  "license": {
    "name": "<must equal the catalog licence (CC-BY-4.0 for the Sardinian catalog)>",
    "attribution": "<attribution text to reproduce>",
    "evidence_url": "<HTTPS page stating the licence>",
    "restrictions": "<optional free text, e.g. unresolved redistribution questions>"
  }
}
```

**Trust semantics.**
- **Verified at import:**
  - `sha256`, `bytes` and the SHA-1 (`version_id`) are recomputed from the staged bytes;
  - URL schemes and hosts;
  - catalog publisher, landing URL and licence;
  - timezone-aware times, with `observed_at ≤ retrieved_at` and no future retrieval.
- **Recorded as claims of the archive, not verified by us:**
  - `observed_at`;
  - `reported_origin_url`.
- **Never claimed:** publisher endorsement, a publication date, or the absence of intermediate
  publisher releases.
- **Rejected:** any key or URL value that looks like a credential (`api_key`, `token`, `cookie`,
  `authorization`, …).
- **GTFS facts are kept apart:** `feed_info` version and validity, and calendar coverage, still
  come from the ZIP and are reported separately from all three times.

## 5. Archive trust policy

- `configs/archive_providers.json` is a reviewed registry of providers, holding record hosts,
  download hosts, the version-id scheme and an `import_enabled` flag. The **official** downloader's
  allowlist (`official.ALLOWED_OFFICIAL_HOSTS`) is unchanged and is checked in a test. A provider
  listing an official host is refused.
- **Transitland is registered with `import_enabled: false` and no download hosts.** Its download
  hosts have not been verified from its documentation, and no download has been authorized. Every
  real archive import is therefore refused today. Enabling it requires a reviewed change (§11).
- Only HTTPS is accepted. There is no `http://` fallback, and no credentials, ports, queries or
  fragments. No live acquisition code exists in Phase 7C. When one is written, it must record the
  final URL after redirects and refuse a final host outside the registry.
- **The modes never cross:**
  - an archive record is refused without `--archive-provenance`, with a hint to pass that flag;
  - an official record is refused with `--archive-provenance`;
  - `--synthetic` together with `--archive-provenance` is refused;
  - the bootstrap importer (`import_file`) refuses archive records unless
    `archive_provenance=True`, and then only with `activate=False`.

## 6. Import path (`manage_snapshots.py import --archive-provenance`)

All Phase 7A controls still apply:
- preview by default;
- `--apply` requires stopped services and takes a verified backup;
- `--real-database` is required for the real database;
- the ZIP and record are staged into a private folder, with a re-hash around staging and
  immediately before the write;
- `expected_sha256` is passed to the importer;
- the import is non-activating, the selection is checked before and after, and failures are
  cleaned up.

In addition:
- the archive record is validated (§4);
- the importer is told `archive_provenance=True`;
- **`feed_sources.download_url` is not updated**: only a publisher-direct import updates it, as
  before;
- an invariant check refuses the result if the source's URL changed anyway.

Two cases are refused and leave the existing evidence untouched:
- duplicate bytes with an identical record are a no-op ("nothing to do");
- the same bytes with a different record.

## 7. Read side: History API and UI, inspector, status

- **`GET /api/v1/history/{source}/snapshots?sort=retrieved|observed`.** The default is `retrieved`,
  as before; an unknown value returns 422. Each item gains `provenance` (kind, label, synthetic,
  publisher, download origin, archive, licence, problem), `observed_at` and `observation_basis`.
  The response gains `sort` and `sort_note`.
  - `observed` orders by observation time, putting unknown times last in retrieval order.
  - Neither order is described as publication chronology.
- **`compare`.** Each side gains `observed_at` and `provenance`. An archived side adds an `info`
  notice: "The baseline is an archived copy from … (archive observation …; obtained locally …),
  originally published by … It was not downloaded from the publisher by this computer."
- **History view:**
  - new columns: *How obtained*, *Observed published* (with its basis) and *Obtained locally*;
  - an *Order snapshots by* selector, defaulting to observation time, with its explanation;
  - snapshot pickers that show kind, observation and local retrieval dates;
  - comparison sides that state both times.
- **Route inspector.** `download_url` is set only for publisher-direct snapshots. An archived copy
  shows its publisher's landing page as context, never the archive URL as an official download.
- **`manage_snapshots.py status`.** Prints `provenance <kind>` per snapshot.

## 8. Backward compatibility

- Existing official sidecars, imports and source rows behave as before. A publisher-direct import
  still records its URL in `feed_sources`, and this is tested.
- Snapshots without a sidecar read as `unknown_legacy`, or `synthetic` for fixtures, and are never
  filled in.
- The schema is unchanged.
- The bootstrap importer's default (`activate=True`) is unchanged.
- The API's default sort is unchanged.
- The catalog gains a top-level `license` (`CC-BY-4.0`, evidence
  `https://www.sardegnamobilita.it/open-data`, observed 2026-11-07), which only the archive
  validation reads.
- The four real snapshots classify as `publisher_direct`, checked in the browser.

## 9. Threat model and how each threat is handled

| threat | handling |
|---|---|
| Third-party ZIP disguised as an official download | No top-level `download_url` is allowed in archive records. Archive download hosts must be registered and must not include official hosts. Official-host URLs are refused as archive URLs. Archive records are refused on the official path. |
| ZIP or record swapped after validation | Phase 7A staging, and a re-check immediately before the write. Tested by replacing the ZIP and forging the record during the backup. |
| Forged or inconsistent SHA-1 | `version_id` must be 40-hex, equal the SHA-1 of the staged bytes, and appear in `record_url`. |
| Downgrade to HTTP, or an unknown archive host | `safe_url` with the provider's hosts, HTTPS only, no port or credentials. |
| Credentials leaking into provenance or logs | Queries and fragments are refused, and credential-like keys or values are refused. No acquisition code exists in this phase. |
| One import overwriting another snapshot's evidence or the source URL | Content-addressed per-snapshot sidecar, refuse-if-different. `download_url` is untouched for archives, with an invariant check. |
| Archived old copy shown as if downloaded in August | Separate `observed_at` and `retrieved_at`, explicit sort labels, and a notice in comparisons. |
| Silent migration of the real database | No schema change, with a frozen schema digest test. The real database hash was checked before and after every run. |
| Bootstrap importer activating an archived copy | `import_file` refuses archive records unless explicitly told, and refuses `activate=True` with them. |

## 10. Verification (local, 2026-11-07)

**Python suite, services stopped:** **251 passed, 0 skipped**. That is 197 earlier tests plus 54 new
cases in `backend/tests/test_phase7c_archive_provenance.py` on SQLite and disposable DuckDB:
- publisher-direct behaviour is preserved;
- an archived copy is attributed to its archive, with the selection and source row unchanged;
- two snapshots keep independent provenance;
- 33 parametrized refusals: source, SHA-256, SHA-1, bytes, times, HTTP or unapproved hosts,
  official URL used as the archive URL, query tokens, redirect host, record URL, observation
  evidence, unknown provider, provider-name mismatch, publisher and landing mismatch, HTTP reported
  origin, licence and attribution, credential keys, missing sections, top-level official fields;
- disabled or overlapping providers;
- the shipped Transitland entry is disabled, and the official allowlist is unchanged;
- the modes never cross;
- idempotent duplicates;
- ZIP replaced during the backup, record forged during the backup, input changed during staging;
- sidecar write failure leaves nothing behind;
- an importer that touches the URL is caught;
- legacy and inconsistent records show as unknown;
- API sorting, labels and same-date comparison;
- status kinds;
- CLI help and preview refusal;
- schema digest.

**Mutation check:** removing the importer's "do not overwrite the official URL" guard fails 10 of
the new tests.

**Real database:** SHA-256 `3c308708…3ca0` and modification time 2026-11-06 17:33:51 +09:00, the
same before and after the suite.

**History browser smoke (Selenium, port 8765, rebuilt production bundle):**
- **`build_history_demo.py --admin-stage archived`:** passed. The invented older v1 is imported
  through `--archive-provenance` from the invented archive (observed 2026-08-28, obtained now)
  while v2 stays current. Checked:
  - labels, licence, and the two times shown separately;
  - the two sort orders differ and are labelled;
  - the same-date 2026-11-06 comparison shows the attribution notice, with trip templates 8 → 9;
  - the selection is unchanged.
- **`preserved` and `activated` stages (Phase 6/7A regression):** passed.

**`verify_local.ps1 -Browser` on the real database (services on port 8765):** passed. Log:
`data/local-verification/phase7c/verify-phase7c-20261107-194929.log`.
- **pytest:** 245 passed, **6 skipped**. The six skips are the real-database read-only tests, which
  cannot open the file while the running backend holds DuckDB's lock. All 251 ran and passed with
  services stopped (above).
- **Startup and build:** backend startup on loopback 4.7 s; production build OK.
- **Browser checks:** all-four-source real smoke, filter races, data-quality checks and the
  real-database History check all passed. Each of the four real snapshots shows as
  "Publisher download", the comparison is explained as impossible, and the selection is unchanged.
- **Real database afterwards:** services stopped, no Python process left. SHA-256 `3c308708…3ca0`
  and modification time 2026-11-06 17:33:51 are unchanged. The selection is still arst
  `52a339e0…`, aspo `2fdf3861…`, atp_sassari `2f029bb3…`, trenitalia_sardegna `9208278…`.

## 11. Limitations

- Only the shape and validation of an archive record exist. There is no acquisition tool, and
  Transitland's download hosts and redirect behaviour are unverified. Its registry entry is
  disabled.
- `observed_at` is the archive's claim. It cannot prove that no intermediate publisher release
  existed.
- Provenance is file-based (§2). Database backups do not include `data/raw`.
- The Quality panel still shows only "Local copy retrieved" for the *active* snapshot. Archived
  copies are never activated by import. If one is ever activated, extending that panel is a small
  follow-up.

## 12. Future steps (not authorized; each needs explicit approval)

**A. Reviewed code or config change to enable Transitland.** Do this before any download:
1. Read Transitland's documentation for the exact feed-version download endpoint and every host it
   may redirect to.
2. Add those hosts to `configs/archive_providers.json` → `transitland.download_hosts` and set
   `import_enabled: true` in a reviewed commit.
3. Add a test for the exact record shape of one Transitland version.
4. If the endpoint needs an API key, it must travel in a header supplied at run time. It must never
   appear in a URL, a file, a log or the record.

**B. Authorization checklist for the older ASPO archive.** What the user must approve:

| # | item | value to approve |
|---|---|---|
| 1 | Eligible access | The user's own Transitland plan that includes historical downloads (e.g. an approved Hobbyist/Academic account for non-commercial use, or Transitland+), and its terms. |
| 2 | Exact archive version | Feed `f-spqb-aspoolbia`, version `b48388a26eeffbadde634b526dc3cb1acd8606ab`. Record page `https://www.transit.land/data/feeds/f-spqb-aspoolbia/versions/b48388a26eeffbadde634b526dc3cb1acd8606ab`. Download URL as documented by Transitland (step A). |
| 3 | Local destination | A new empty folder, e.g. `data/downloads/archive/transitland/aspo/` (git-ignored), never `data/raw` directly. |
| 4 | Licence | CC BY 4.0, attribution required (Transitland listing and the region's open-data page). Attribution text naming ASPO S.p.a. / Sardegna Mobilità and the Transitland archive. Note that the Atlas record leaves `redistribution_allowed` unset. |
| 5 | Expected SHA-1 | `b48388a26eeffbadde634b526dc3cb1acd8606ab`. Abort on mismatch. |
| 6 | SHA-256 | Computed after download and reported, then written into the record. Never invented in advance. |
| 7 | Validation | MobilityData GTFS validator, read-only. Report its errors and warnings. |
| 8 | Real database backup | Taken automatically by `manage_snapshots.py import --archive-provenance --real-database --apply`, after a preview (without `--apply`) has been reviewed. Services stopped. |
| 9 | Non-activating import | Confirm afterwards that `aspo` remains selected at `2fdf386102741d57c5045190da363097` and `feed_sources.download_url` for `aspo` is unchanged. |
| 10 | Same-date comparison | History, `same_date` on 2026-11-06, baseline `b48388a2…` (archived), comparison the current `a710bc7c…` snapshot. If the older feed has no service that day, report that; do not switch dates silently. |

Each row is a separate approval. Nothing in Phase 7C performs any of them.

## 13. Files changed

- **New:**
  - `backend/transport_atlas/archive_provenance.py`
  - `configs/archive_providers.json`
  - `backend/tests/archive_fixtures.py`
  - `backend/tests/test_phase7c_archive_provenance.py`
  - `docs/PHASE7C_ARCHIVE_PROVENANCE.md`
- **Backend:** `ingest.py`, `admin.py`, `history.py`, `api.py`, `route_details.py`
- **CLI and scripts:**
  - `scripts/manage_snapshots.py` (`--archive-provenance`, status kind)
  - `scripts/build_history_demo.py` (`--admin-stage archived`)
  - `scripts/smoke_history_ui.py` (archived checks; real-mode provenance check)
- **Frontend:** `frontend/src/api.ts`, `History.tsx`, `styles.css`
- **Config and docs:**
  - `configs/sardinia_sources.json` (catalog `license`)
  - `README.md`
  - `docs/IMPLEMENTATION_STATUS.md`
