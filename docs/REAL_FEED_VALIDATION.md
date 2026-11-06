# Official Sardinia feed acquisition and verification

## Scope
Current Windows-local evidence and commands are recorded in
[`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md). GitHub Actions are
unavailable for this development session; do not use remote workflows for checks.
Run `scripts/import_local_feeds.py --sources aspo,trenitalia_sardegna,arst,atp_sassari` for
resumable shared-database imports and `scripts/verify_local.ps1` for local tests.
Raw downloaded bytes, provenance, reports and databases remain under ignored `data/`.

The official operator pages are cataloged in `configs/sardinia_sources.json`.
We do **not** hard-code guessed ZIP URLs: they are discovered from the
publisher's actual landing page at download time, and the actual final URL and
SHA-256 checksum are preserved next to the ZIP.

**Never assume** catalog validity ranges mean all routes run on all days.
Schedules are calculated from GTFS calendars and exceptions after import.

## Local quickstart (backend server must be stopped before imports)

```powershell
cd C:\Users\User\Desktop\TransportAtlas
.\.venv\Scripts\python.exe -m transport_atlas fetch-official --source aspo --discover-only
.\.venv\Scripts\python.exe -m transport_atlas fetch-official --source aspo
.\.venv\Scripts\python.exe -m transport_atlas import --file data\downloads\aspo.zip --source aspo --publisher "ASPO Olbia" --landing-url https://www.sardegnamobilita.it/open-data/quadri-orari-aspo
.\.venv\Scripts\python.exe -m transport_atlas summary --date 2026-11-06
```

For ARST replace `aspo` with `arst`, and similarly use `atp_sassari` or
`trenitalia_sardegna`. The ZIP import and archive remain atomic and source-scoped.
Stop the FastAPI server before writing to DuckDB, then restart it.

If the site no longer provides a directly discoverable resource, use the
official landing page to download the GTFS ZIP manually and run the existing
`import --file` command. Do not fabricate a download URL. `--resource-url` is
available only for verified HTTPS URLs on the official or source-specific approved
operator hosts in the manifest.

## Verification process

1. Save official landing page URL and dataset ID (in manifest).
2. Discover exact resource links and download official ZIP with bounded transfer.
3. Record final URL, UTC retrieval date, file SHA-256 and byte size.
4. Run importer checks and inspect source-specific warning counts.
5. Run service-date summary, e.g. 2026-11-06.
6. Compare route, trip, stop, and shape counts to the provider's raw GTFS ZIP.
7. Inspect diverse service dates (weekday, Sunday, holiday) in the dashboard.
8. Retain earlier feed ZIPs for reproducibility; don't publish raw feeds without licensing review.

The historical GitHub compatibility probe is retained in source for reference.
Current verification is local: ZIP CRC/SHA-256, raw/database record reconciliation,
weekday/weekend summaries, API timing and browser checks. **Upstream failures do
not imply successful imports.** No remote job is needed or triggered.

## Known limitations

- Live website may change page markup or move its download endpoint.
- Some official resources can be blocked or require a browser; manual ZIP import
  remains supported.
- Real datasets can require more memory and have unsupported optional GTFS features.
- Provider licensing audit and persisted historical pattern management remain incomplete.
- Rendering whole networks in GeoJSON remains bounded: truncated results warn,
  but large-country vector tiling is a later milestone.

## Historical GitHub compatibility status (2026-11-06)

The GitHub-hosted four-source probe's **final report** independently verified the
following actual imports and date-specific summaries for 2026-11-06:

| Source | Routes | Stops | Scheduled trip instances | Status |
|---|---:|---:|---:|---|
| ARST | 322 | 3450 | 4731 | Verified (import took ~651s) |
| ASPO Olbia | 15 | 422 | 319 | Verified |
| Trenitalia Sardegna | 2 | 42 | 200 | Verified |
| ATP Sassari | — | — | — | **Not verified**: operator download redirects to non-HTTPS Google URL |

These are provider-local record counts, not spatially deduplicated regional totals.
The full-run workflow was marked **cancelled** near its old 12-minute limit,
although its import, artifact and PR comment steps recorded success. See
[PR #2](https://github.com/Wanderkind/TransportAtlas/pull/2) and its
[verification run](https://github.com/Wanderkind/TransportAtlas/actions/runs/42164322888).

Do **not** follow the historical ATP Sassari redirect by weakening URL checks or
assume an older ZIP covers the research date. The current local run independently
found `https://atpsassari.it/media/files/atpss-gtfs.zip` on an approved operator host.
Its raw calendars cover 2026-10-20–2026-12-31, and its verified November 6 import has
50 routes, 1,032 stops and 1,023 scheduled trips. This supersedes the historical
unverified designation without accepting the insecure redirect. Licensing is
still not asserted for redistribution.

These historical results do not substitute for a local import. The current
per-source local results, valid dates, archive hashes and measured timings are
documented in `IMPLEMENTATION_STATUS.md` and `data/local-verification/feeds.json`.
The local importer uses bulk COPY for DuckDB while retaining transaction rollback
and the SQLite test fallback. Native Windows certificate chain validation solves
missing-intermediate issues without disabling HTTPS or redirect restrictions.
