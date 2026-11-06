# Network atlas — local development, 2026-11-06

The Windows checkout now contains verified ASPO, Trenitalia Sardegna, ARST and ATP
Sassari snapshots in one local DuckDB. See [implementation status](IMPLEMENTATION_STATUS.md)
for source counts, hashes, validity periods, commands and measured results. No
GitHub Actions, remote writes or public deployment were used.

## Geography

`GET /api/v1/network/geometry?date=2026-11-06` returns all matching source-scoped
alignments, with explicit route/point budgets, truncation status and quality tags.
`source=ID` selects one operator; `sources=ID,ID` scopes independent toggles before
limits are applied. Modes are `all`, `bus`, `rail`, `tram` and `ferry`.

The four-source weekday view has 349 operating routes, 2,596 alignments and 119,406
points, with no truncation or missing route geometry. All 389 imported route
records can be requested by disabling the operating-date-only filter. However,
35 ARST and 4 ATP route records have no trip references. The all-dates map therefore
has 350 available route geometries, not 389. Missing trip data does not establish
absence of transport service. The importer
retains original shapes. The regional view applies radial/Douglas–Peucker
simplification and increases its tolerance across the entire selected network
before enforcing a cap, so the first source cannot consume the budget silently.
The observed regional tolerance bound is 0.0017 degrees. Selected trips use their
original source alignment instead.

Missing shapes produce dashed schematic stop links for each distinct stop
sequence; these are not actual road/rail alignments. ARST has 156 trip-template
warnings for missing shapes. Directions are operator categories, not compass
directions. Route highlighting preserves dashed schematic styling.

MapLibre uses a bundled local worker, clustered stop layers, source/mode/date
filters, operator toggles, route and server-side stop search, route selection,
direction highlighting, variant geometry and fit controls. Stop markers follow
the selected scope. Search and source filters apply before the stop response cap.
The optional OpenFreeMap street style provides context and falls back to the
offline-compatible plain map. `?basemap=plain` starts without external tiles.

## Timetables and provenance

The route inspector groups trips by snapshot, route, direction, ordered boarding/
alighting stop sequence and shape. It shows daily precise-trip counts, full-route
first/last departures, per-variant median origin departure intervals, ordered
arrival/departure times, operating calendars, exceptions and snapshot provenance.
Publisher-declared `feed_info` validity is displayed separately from observed
calendar coverage. Historical download URLs come from their snapshot sidecars.

GTFS hours above 24:00 remain intact. Stop boards use stop-local wall dates and
include previous-service-day overnight trips. Exact frequency instances preserve
template offsets; non-exact windows never become fabricated precise departures.
Template times are labeled when no exact operating instance is selected.

## Performance and limits

Snapshot/filter-keyed serialized responses use a bounded 16-entry memory cache
and disposable disk files under `data/cache/network`. Simplified shapes are reused
within a backend process. Corrupt disk responses are rebuilt. Batched frequency
lookups avoid one SQL query per scheduled trip in summaries and stop boards.

Uncached regional queries still take tens of seconds, and an uncached new date
or filter can require another build. This is not national-scale vector tiling.
The UI reports truncation instead of claiming complete coverage when caps are
reached. Route departure lists are capped at 200, with full-route statistics
reported separately; direction/variant list filters apply to that loaded subset.
No broad p95/frame-rate guarantee is claimed from the small local timing sample.

Next steps: persist reusable multiresolution shape data, add viewport loading or
vector tiles, complete timetable pagination and measure cold date transitions.
