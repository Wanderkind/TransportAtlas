# Transport Atlas

Private, local-first GTFS Schedule importer and public-transport research dashboard. **Version 0.1.0 is the M0 + initial M1 implementation, not the full agreed MVP.** Its core use is importing real agency GTFS feeds into source-isolated snapshots, querying scheduled service by date, and exploring stops and routes in a preliminary browser UI.

## What works

- Import a GTFS ZIP from the local filesystem; reject unsafe ZIPs, invalid key references, and duplicate records.
- Archive SHA-256 identified original feed bytes, with one selected snapshot per source and no cross-feed key collisions.
- Record basic agencies, routes, stops, trips, stop times, weekly calendars, date exceptions, shapes, and frequencies in SQL.
- Query scheduled-service counts on a GTFS service date, exact departures on a stop-local date (including trips whose GTFS time exceeds 24 hours), and non-exact headway windows **without inventing departures**.
- Expose read-only FastAPI endpoints with date-aware route flags, stops, geometry, and stop departure boards.
- Run a starter React + MapLibre browser atlas to select routes, inspect stops, and view service summaries. The frontend is a scaffold and has not yet been built in this offline execution environment.

**Not yet complete:** verified four-operator live imports, MobilityData external validator integration, dataset-selection history UI, pattern grouping, advanced statistics, complete feed coverage, imported licensing metadata, sophisticated maps/tiles, exports, and production performance improvements.

## Requirements

Windows 10/11, Python 3.11 or later (3.12 recommended), Node.js 20 or later, and network access for the initial dependency install. Actual GTFS ZIPs must be obtained separately from a data provider. No accounts, API keys, cloud services, or public deployment are needed.

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

The map uses an offline-capable blank base style by default. To show roads/place names, set `VITE_BASEMAP_STYLE_URL` to a licensed style URL (internet and provider terms apply) before starting Vite. Without a basemap, imported route shapes and stops still use geographic coordinates.

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

The source includes generated synthetic test feeds and test cases for weekly plus exception calendars, calendar-dates-only feeds, duplicate IDs across sources, 25:15:00 stop times, exact and non-exact frequencies, Europe/Rome DST, unsafe ZIPs, Unicode IDs, and transactional failure preservation. The CI workflow executes the Python suite but has not been run on GitHub yet.

## Project references

- [Detailed approved MVP specification](docs/MVP_SPEC.md)
- [Initial milestone report](docs/IMPLEMENTATION_STATUS.md)
- [GTFS reference](https://gtfs.org/documentation/schedule/reference/)
- [Sardegna Mobilità](https://www.sardegnamobilita.it/open-data/trasporti)

## Optional: publish as a new private GitHub repository

The artifact is not yet pushed anywhere. After inspecting the source and creating a private repository named `Wanderkind/TransportAtlas`, you can publish it using the [GitHub CLI](https://cli.github.com/) from the project directory:

```powershell
git init -b main
git add .
git commit -m "Initial Transport Atlas GTFS prototype"
gh repo create Wanderkind/TransportAtlas --private --source . --remote origin --push
```

If you use the included `.bundle` archive instead of the ZIP, the initial local commit already exists; do **not** run `git init` again. Clone the bundle and run `gh repo create ... --source . --remote origin --push` from the cloned directory. Check that the repository is **private** before pushing.
