# Implementation status — 2026-11-06 (v0.2 development)

## Implemented and tested

- M0: repository, Python package, React + MapLibre frontend, synthetic fixture, GitHub CI.
- Initial M1: source-specific archived GTFS snapshots, calendars/exception handling, overnight services, exact/non-exact frequency distinction.
- M2 preview: simultaneous route geometry, source/mode/date filters, map route selection and highlighting, source vs schematic geometry, online street basemap with offline-compatible plain fallback.
- Initial M3: route-level service-date departures, direction filter and stop boards (wall-date-based); no journey planner.

## Verification

- Local test environment: **19 passed, 1 skipped**; skipped test requires DuckDB, which was unavailable in the tool container.
- GitHub Python test workflow: passed for v0.2 PR head `1ed560a`.
- GitHub frontend TypeScript/Vite production build: passed for v0.2 PR head `1ed560a`.
- GitHub Chrome smoke: passed for v0.2 PR head `1ed560a`. It imported the fictional GTFS feed, opened the UI, selected the overnight route, confirmed the inspector and route departure, and toggled the basemap.
- No actual ARST/ATP/Trenitalia/ASPO feed was imported. No external GTFS validation was run. Performance and completeness on real regional feeds remain unverified.

## Limitations / future work

1. Import and validate official GTFS ZIPs; preserve source URLs, retrieved dates, licensing and content hashes. Manual download required until a verified source downloader exists.
2. Profile geographic geometry queries on real feeds; implement viewport/bbox filtering, route simplification or vector tiles for national coverage.
3. Add pattern-level grouping, real route-variant selection, and full stop-by-stop timetables. Route direction IDs must not be interpreted as geographic directions.
4. Avoid misinterpretation of capped geometry and stop results; page/stream large geographic datasets. The current metadata includes a truncation flag.
5. Add historical feed comparison, connectivity analysis, and export formats; expand beyond Sardinia.
6. Browser and DuckDB tests need eventual real Windows environment confirmation. Chrome CI does not substitute for validating actual imported transport data.

**Deployment recommendation:** suitable for local development and demonstration, not for public or research-critical production release.
