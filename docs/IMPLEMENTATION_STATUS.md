# Implementation status — 2026-11-06

**Completed in this development artifact**

- M0: project layout, Python package, read-only local API, React/MapLibre source scaffold, initial CI workflow, setup instructions, synthetic GTFS fixtures.
- Initial M1: local GTFS archive/import, stable source+checksum snapshot identity, source-scoped raw IDs, source selection pointer, calendar and overnight schedule engine, exact/non-exact frequency separation, shape/schematic API, basic QA warnings.
- Unit/API test suite and CLI smoke test pass under the offline SQLite test adapter. Results: 14 passed, 1 skipped (target DuckDB adapter not present). Three TypeScript files passed syntax-only transpilation, but dependency-aware build remains untested.

**Verification limitations**

- The execution environment could not download DuckDB; DuckDB adapter exists but its behavior is not yet runtime-verified.
- Node dependencies were not available for the React/MapLibre build; TypeScript build and browser behavior are not verified.
- A direct attempt to retrieve an ASPO ZIP was unsuccessful. No real Sardinian GTFS ZIP was imported; the tested fixture is synthetic.
- No external MobilityData GTFS Validator was executed; the internal importer performs a limited subset of validation.
- GitHub repository creation and remote CI were not performed; this package can be initialized as an independent private repository.

**Next highest-priority actions**

1. Install dependencies on Windows and run tests against DuckDB, not only SQLite.
2. Run npm install / npm run build and fix any TypeScript or MapLibre styling issues.
3. Import a real ARST feed and inspect unsupported GTFS fields or data-quality problems. Measure import time and record counts.
4. Add independent MobilityData validator and safe download/metadata workflow.
5. Complete regional route visualization and date-aware pattern inspector, then repeat ingestion for ATP Sassari, ASPO Olbia and Trenitalia Sardegna.
6. Implement more rigorous frequency/headway analytics, route-pattern grouping and cross-feed interoperability.

**Status:** M0 partly complete (frontend build unverified); initial M1 slice implemented and fixture-tested. The full MVP M1–M4 is not finished.
