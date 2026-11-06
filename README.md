# TransportAtlas

Local-first GTFS Schedule research dashboard: Python, FastAPI, DuckDB, React,
TypeScript, Vite and MapLibre. Explore imported transport networks, route variants,
ordered trip stops and calendars. This is a research atlas, not a journey planner
or a claim of complete real-world transport coverage.

**Development and verification run locally. GitHub Actions are not required.**
No accounts, paid infrastructure, public deployment or API keys are needed.

## Install on Windows

Use Python 3.12 (minimum 3.11) and Node.js 20.19+ or 22.12+. The local audit used
Python 3.12, Node 24.19.0 and npm 11.17.0. System `python` may select an older
version; use the virtual environment explicitly.

```powershell
cd C:\Users\User\Desktop\TransportAtlas
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,browser]"
cd frontend
npm ci
cd ..
```

Skip installation when the existing environment works. The browser extra requires
Chrome; Selenium Manager obtains its driver on first use. `tzdata` supplies IANA
timezone rules on Windows. Official downloads use native Windows certificate
validation through `truststore`; HTTPS verification and source allowlists remain
enforced. Frontend versions are locked in `frontend/package-lock.json`.

## Import actual Sardinian feeds

**Stop the backend before importing.** The official workflow discovers publisher
links from Sardegna Mobilità and enforces approved HTTPS hosts and redirects.

```powershell
.\scripts\stop_local.ps1
.\.venv\Scripts\python.exe scripts/import_local_feeds.py --sources aspo,trenitalia_sardegna,arst,atp_sassari
.\.venv\Scripts\python.exe -m transport_atlas summary --date 2026-11-06
```

All feeds coexist in `data/transport_atlas.duckdb`, with source-scoped identities.
The checker reuses cached ZIPs, verifies SHA-256 and ZIP CRC, reconciles raw/database
table counts and evaluates Friday through Monday service calendars. Evidence is
saved to `data/local-verification/feeds.json`.

Recheck cached data without network access or replacing the original timing report:

```powershell
.\.venv\Scripts\python.exe scripts/audit_local_evidence.py --output data/local-verification/phase3/manual-audit.json
```

The audit reads raw CSV tables and opens DuckDB read-only; stop the backend first.
Unchanged imports do not reinsert data. To intentionally retrieve a newer feed,
use the official CLI; earlier content-addressed archives remain available:

```powershell
.\.venv\Scripts\python.exe -m transport_atlas fetch-official --source aspo --discover-only
.\.venv\Scripts\python.exe -m transport_atlas fetch-official --source aspo
.\.venv\Scripts\python.exe -m transport_atlas import --file data\downloads\aspo.zip --source aspo --publisher "ASPO Olbia" --landing-url https://www.sardegnamobilita.it/open-data/quadri-orari-aspo
```

Manual ZIP import is supported. Without a download sidecar, its timestamp means
local import time, not verified upstream retrieval. Reimporting an already archived
older snapshot does not automatically reactivate it. Raw ZIPs retain optional GTFS
fields that the normalized model does not use; the checker lists those fields and
declared `feed_info`. Review provider licensing before redistributing feed bytes.

ATP Sassari is now locally verified from a directly discovered approved HTTPS
operator URL, `https://atpsassari.it/media/files/atpss-gtfs.zip`, with calendars
covering October–December 2026. The earlier unapproved HTTP Google redirect was
not used. No insecure redirect or expired substitute is accepted. Unavailable
data does not establish absence of services.
See [feed verification](docs/REAL_FEED_VALIDATION.md) and
[implementation status](docs/IMPLEMENTATION_STATUS.md) for measured results.

## Start and stop

Launch both services in hidden local processes:

```powershell
cd C:\Users\User\Desktop\TransportAtlas
npm run build --prefix frontend
.\scripts\start_local.ps1 -Production
# http://127.0.0.1:5173/?date=2026-11-06
# Offline map: http://127.0.0.1:5173/?date=2026-11-06&basemap=plain
.\scripts\stop_local.ps1
```

The launcher checks ports, waits for HTTP readiness and saves owned process IDs in
`data/local-services.json`. The stop script verifies ownership before stopping
them. Logs are `data/backend.*.log` and `data/frontend.*.log`. Both services bind
only to `127.0.0.1`. Omit `-Production` for the Vite development server with live
source updates. Restart the backend after imports and rebuild production assets
after frontend changes.

Alternatively, use two terminals and stop each with **Ctrl+C**:

```powershell
# Terminal 1, repository root
.\.venv\Scripts\python.exe -m transport_atlas serve
# Terminal 2
cd C:\Users\User\Desktop\TransportAtlas\frontend
npm run dev
```

API health: `http://127.0.0.1:8000/api/v1/health`; API docs:
`http://127.0.0.1:8000/docs`.

## Explore

The [Phase 3 handoff](docs/PHASE3_HANDOFF.md) and corrective review record the
resolved MapLibre rendering blocker, green local browser verification and the
remaining latency limits. Merging is not authorized by these documents.
The [Phase 3 corrective review](docs/CORRECTIVE_REVIEW_20261106.md) records the
current independent audit, browser regressions and performance limits. This phase
was initially local-only. The later checkpoint push authorization does not
authorize GitHub Actions or a main merge.

- Display imported sources together, toggle operators or select one source.
- Filter routes and stops by date and bus, rail, tram or ferry mode. Disable
  “Operating on selected date only” to include inactive imported routes.
- Search routes and stops, click lines/markers, zoom into stop clusters and use
  **Fit** for the displayed network or focused variant.
- Select directions and stop-sequence/shape variants. Click an exact departure
  for ordered arrival/departure times and the trip's original geometry.
- Inspect first/last departures, daily precise-trip counts, per-variant median
  departure intervals at the first stop, calendars, exceptions and provenance.
- **Street map** uses OpenFreeMap for geographic context; **Plain map** makes no
  external tile requests. Online failures fall back to plain mode.

**Dashed schematic links are not actual road/rail alignments.** Regional source
shapes use adaptive display simplification within an explicit point budget; the
current four-source regional overview uses a conservative 0.0017-degree bound.
Selected-trip geometry retains the original points. The UI discloses limits and
unavailable/expired data. Source
coverage totals describe whole selected feeds; mode filters apply to the map and
route list. Stops from different feeds are not automatically deduplicated.

There are 389 imported route records, but 35 ARST and 4 ATP records have no trip
references. Their geometry cannot be inferred from these feeds. All 350 routes
with trips can be mapped without truncation; 349 operate on November 6. Missing
trip data is not proof of absent real-world service.

The coverage panel reconciles the selected source/mode scope: records = no trips
+ inactive excluded + eligible; eligible = drawn + insufficient geometry + omitted
by limits. Partial routes are a subset of drawn routes. Date/mode/operator changes
hide previous geometry, stops and inspectors immediately; loading and errors refer
to the current filters. Successful results are cached by their exact filter identity.

GTFS hours beyond 24:00 are preserved. Stop-local boards include preceding-day
overnight services where applicable. `exact_times=0` frequencies remain windows,
never invented precise departures; `exact_times=1` instances use template stop
offsets. Variant template times are explicitly labeled until an exact instance is
selected. Route timetables are paged (200 departures per page, **Load more
departures**). Direction and variant filters are applied by the server to the
complete instant-ordered list before paging, so every filtered page is exact.
Summary statistics always cover the full route.

## Verify locally

```powershell
.\scripts\verify_local.ps1
# Independent raw ZIP/read-only SQL audit: backend must be stopped.
.\scripts\verify_local.ps1 -Feeds
# Real-data browser smoke: both services must be running.
.\scripts\verify_local.ps1 -Browser
.\.venv\Scripts\python.exe scripts/profile_local.py
```

Individual checks:

```powershell
.\.venv\Scripts\python.exe -m pytest -q backend/tests
.\.venv\Scripts\python.exe scripts/check_startup.py
npm run build --prefix frontend
.\.venv\Scripts\python.exe scripts/smoke_real_ui.py
.\.venv\Scripts\python.exe scripts/smoke_filter_races.py
```

Tests cover DuckDB, calendar exceptions, overnight/DST semantics, source isolation,
unsafe ZIPs, atomic failure preservation, exact/non-exact frequencies, bulk-load
fidelity/rollback and historical provenance. Startup testing uses an isolated
temporary DuckDB and the actual Uvicorn launcher. Browser smoke uses real imported
feeds and saves screenshots and timing evidence to `data/local-verification/`.
Phase 3 evidence lives in its `phase3/` subdirectory. Browser tests inspect actual
MapLibre sources and rendered features, block HTTPS in plain-map mode, and inject
delayed, out-of-order and failed responses. Add `&diagnostics=1` to a dashboard URL
to enable read-only map diagnostics used by these tests. Selenium and a working
local Chrome installation are required; no remote browser service is used.
The older `scripts/smoke_ui.py` expects the fictional fixture and an isolated demo DB.

## Storage and remaining scope

| Path | Purpose |
|---|---|
| `data/transport_atlas.duckdb` | Shared local database |
| `data/downloads/` | Latest ZIPs and verified download sidecars |
| `data/raw/<source>/<sha256>.zip` | Immutable feed archives and sidecars |
| `data/local-verification/` | Count/calendar evidence, timings and screenshots |
| `data/local-verification/phase3/services/<run>/` | Unique backend/frontend launch logs |
| `data/cache/network/` | Disposable, snapshot/filter-keyed serialized GeoJSON |
| `data/cache/shapes/` | Disposable per-snapshot simplified shape levels (`<id>.json` + small `--` deltas) |
| `.venv/`, `frontend/node_modules/`, `frontend/dist/` | Local environment/builds |

These paths are ignored by Git. Only synthetic test ZIPs belong in the repository.
Map responses are cached in memory and on disk; selection and filter changes use
different keys. Invalid cache files are rebuilt. Simplified shape levels are also
stored per immutable snapshot, so after the very first regional build (about a
minute) a new date usually builds in a few seconds, even after a restart. Cache
files can be removed while services are stopped to force rebuilding.
The application uses bounded GeoJSON, not national vector tiles. External
MobilityData validation, stop-board pagination, historical selection UI, exports,
physical-stop matching, connectivity/accessibility analysis and multi-country
validation remain future milestones. See the [MVP specification](docs/MVP_SPEC.md).
