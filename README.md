# Transport Atlas

Private, local-first GTFS Schedule importer and public-transport research dashboard. **Version 0.2 development branch adds multi-route visualization and timetable inspection; it is not yet the full agreed MVP.** Its core use is importing real agency GTFS feeds into source-isolated snapshots, querying scheduled service by date, and exploring stops and routes in a preliminary browser UI.

## What works

- Import a GTFS ZIP from the local filesystem; reject unsafe ZIPs, invalid key references, and duplicate records.
- Archive SHA-256 identified original feed bytes, with one selected snapshot per source and no cross-feed key collisions.
- Record basic agencies, routes, stops, trips, stop times, weekly calendars, date exceptions, shapes, and frequencies in SQL.
- Query scheduled-service counts on a GTFS service date, exact departures on a stop-local date (including trips whose GTFS time exceeds 24 hours), and non-exact headway windows **without inventing departures**.
- Expose read-only FastAPI endpoints with date-aware route flags, stops, geometry, and stop departure boards.
- Run a React + MapLibre browser atlas showing simultaneous imported route geometry, date/mode/source filters, route highlighting, stop inspectors, and route timetable summaries. The frontend build is checked in a separate GitHub Actions workflow.

**Not yet complete:** verified four-operator live imports, MobilityData external validator integration, dataset-selection history UI, pattern grouping, advanced statistics, complete feed coverage, imported licensing metadata, sophisticated maps/tiles, exports, and production performance improvements.

## Requirements

Windows 10/11, Python 3.11 or later (3.12 recommended), Node.js 20 or later, and network access for the initial dependency install. Actual GTFS ZIPs must be obtained separately from a data provider. No accounts, API keys, cloud services, or public deployment are needed.

## Official Sardinia GTFS sources (experimental)

After installing the backend, use `python -m transport_atlas fetch-official --source aspo` to download a feed from the official publisher. Add `--discover-only` to inspect discovered resource URLs first. Follow [the validation guide](docs/REAL_FEED_VALIDATION.md) for source attribution, provenance, and imports. If a download link is not discoverable, download the GTFS ZIP manually from the official landing page and use the existing `import --file` workflow. **No real operator data is bundled.**

## Getting started (Windows PowerShell)

```powershell
cd D:\Path\To\transport-atlas
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

If PowerShell execution policy prevents activating the virtualenv, use `.\.venv\Scripts\python.exe` explicitly instead of changing your security policy.

**Import the included *synthetic* demonstration dataset:**

```powershell
python -m transport_atlas import --file backend\tests\fixtures\synthetic_sardinia.zip --source synthetic_demo --publisher "SYNTHETIC (not real service)"
python -m transport_atlas summary --date 2026-11-06
python -m transport_atlas sources
python -m pytest -q backend/tests
```

The fixture is completely fictional. The displayed stop locations are approximate coordinates for testing the interface, not evidence of actual services.

**Import an actual GTFS Schedule ZIP:**

1. Download a ZIP from the [Sardegna Mobilità catalog](https://www.sardegnamobilita.it/open-data/trasporti). Confirm that the ZIP contains `agency.txt`, `routes.txt`, etc., and inspect its license/attribution terms.
2. Import the downloaded ZIP; for example:

```powershell
python -m transport_atlas import --file "C:\Downloads\arst-gtfs.zip" --source arst --publisher ARST --landing-url https://www.sardegnamobilita.it/open-data/trasporti
python -m transport_atlas summary --date 2026-11-06
```

Do not assume the example filename corresponds to an actual provider filename or that a feed necessarily covers the selected date.

**Run the backend** (first terminal):

```powershell
python -m transport_atlas serve
# Local API health: http://127.0.0.1:8000/api/v1/health
# OpenAPI docs:      http://127.0.0.1:8000/docs
```

**Run the React frontend** (second terminal):

```powershell
cd frontend
npm install
npm run dev
# http://127.0.0.1:5173
```

The map defaults to the [OpenFreeMap Liberty](https://openfreemap.org/quick_start/) online street basemap; use the **Plain map** button for an offline-compatible blank map. The app automatically falls back to plain mode if the online style cannot load. Actual route geometry and timetables remain local.

Stop the backend before running an import command against the same DuckDB file; concurrent cross-process write access is not yet supported. Restart the backend to load the updated selection.

## CLI

```text
python -m transport_atlas [--db PATH] [--backend duckdb|sqlite] COMMAND

Commands:
  import --file ZIP --source ID [--publisher TEXT] [--landing-url URL] [--license NAME] [--archive PATH]
  summary --date YYYY-MM-DD
  departures --snapshot SHA --stop ID --date YYYY-MM-DD [--start-hour N] [--end-hour N]
  sources
  serve [--port 8000]
```

DuckDB is the default. SQLite is supported exclusively as a zero-extra-dependency **test fallback**, for example `python -m transport_atlas --backend sqlite --db demo.sqlite summary --date 2026-11-06`; do not treat SQLite performance or spatial support as equivalent to DuckDB.

## Design choices and caveats

- The dataset identifies individual GTFS records by `(snapshot_id, GTFS ID)`; identical IDs in different feeds never collide.
- A service date is not necessarily the same as the wall-clock date of an arrival or departure. GTFS times are stored as integer seconds and converted using the agency timezone and the noon-minus-12-hours service-day convention.
- `frequencies.txt` entries with `exact_times=1` are expanded into scheduled instances; `exact_times=0` are labeled headway windows, **not** precise departures. Frequency-window estimates are not conflated with confirmed trip counts.
- Geographic geometry from `shapes.txt` is identified as source geometry; when unavailable, direct stop-to-stop lines are explicitly labeled *schematic*.
- Date-range warnings indicate only what is contained in the currently imported data; they do not prove a route is absent in the real world.
- The simple API selects the most recently imported snapshot for each source. Reimporting an older snapshot already archived does not automatically reactivate it. Full selection-version management is planned.
- The initial importer intentionally supports scheduled fixed-stop GTFS feeds, not GTFS-Flex; large or complex national feeds may need additional validation, optimization, and optional-field handling.

## Development and testing

```powershell
python -m pytest -q backend/tests
```

The source includes generated synthetic test feeds and test cases for weekly plus exception calendars, calendar-dates-only feeds, duplicate IDs across sources, 25:15:00 stop times, exact and non-exact frequencies, Europe/Rome DST, unsafe ZIPs, Unicode IDs, and transactional failure preservation. GitHub Actions contains separate Python test and frontend build workflows.

## Project references

- [Detailed approved MVP specification](docs/MVP_SPEC.md)
- [Initial milestone report](docs/IMPLEMENTATION_STATUS.md)
- [v0.2 geographic atlas implementation and limitations](docs/V02_NETWORK_ATLAS.md)
- [GTFS reference](https://gtfs.org/documentation/schedule/reference/)
- [Sardegna Mobilità](https://www.sardegnamobilita.it/open-data/trasporti)

## GitHub repository

The project is maintained at [Wanderkind/TransportAtlas](https://github.com/Wanderkind/TransportAtlas). Check the repository visibility before sharing imported data or licensing-restricted documents. The repository should contain **code and synthetic fixtures only**, never provider data until reuse rights are established.
