# Official Sardinia feed acquisition and verification

## Scope
The official operator pages are cataloged in `configs/sardinia_sources.json`.
We do **not** hard-code guessed ZIP URLs: they are discovered from the
publisher's actual landing page at download time, and the actual final URL and
SHA-256 checksum are preserved next to the ZIP.

**Never assume** catalog validity ranges mean all routes run on all days.
Schedules are calculated from GTFS calendars and exceptions after import.

## Local quickstart (backend server must be stopped before imports)

```powershell
cd C:\Users\User\TransportAtlas
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
available only for a verified HTTPS URL on the official Sardegna Mobilità host.

## Verification process

1. Save official landing page URL and dataset ID (in manifest).
2. Discover exact resource links and download official ZIP with bounded transfer.
3. Record final URL, UTC retrieval date, file SHA-256 and byte size.
4. Run importer checks and inspect source-specific warning counts.
5. Run service-date summary, e.g. 2026-11-06.
6. Compare route, trip, stop, and shape counts to the provider's raw GTFS ZIP.
7. Inspect diverse service dates (weekday, Sunday, holiday) in the dashboard.
8. Retain earlier feed ZIPs for reproducibility; don't publish raw feeds without licensing review.

CI's `Official GTFS compatibility probe` uses the smaller ASPO and ATP Sassari
feeds to test a real download/import path when the provider is reachable from
GitHub-hosted runners. It creates a report artifact and a PR comment with a clear
verified/unverified designation; **upstream failures do not imply successful
imports**. The raw feeds and DuckDB files are never uploaded or committed.

## Known limitations

- Live website may change page markup or move its download endpoint.
- Some official resources can be blocked or require a browser; manual ZIP import
  remains supported.
- Real datasets can require more memory and have unsupported optional GTFS features.
- The application currently lacks provider licensing audit and route-pattern deduplication.
- Rendering whole networks in GeoJSON remains bounded: truncated results warn,
  but large-country vector tiling is a later milestone.

## Recorded live compatibility status (2026-11-06)

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

Do **not** follow the ATP Sassari redirect by weakening URL checks or assume an
older archived GTFS ZIP covers 2026-11-06. Keep the source unverified until a
valid official or explicitly documented, appropriately licensed feed for that
service date can be retrieved and imported.

**CI policy:** Routine PR verification checks ASPO, ATP Sassari and Trenitalia.
ARST has a slower import and is omitted from routine probe jobs; to recheck the
full set, use GitHub Actions → Official GTFS compatibility probe → Run workflow,
set `sources` to `arst,aspo,atp_sassari,trenitalia_sardegna`.
The timeout is increased to 18 minutes. A green CI job only means the probe ran;
the **per-source verified statuses** are authoritative.
