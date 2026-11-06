# Transport Atlas — v0.2 geographical network visualization

Status: implementation candidate, pending remote CI and real-data validation.

## Included

- `GET /api/v1/network/geometry?date=YYYY-MM-DD&mode=all|bus|rail|ferry&active_only=true|false&source=ID`
  returns a GeoJSON FeatureCollection of all matching route shapes and a `metadata` object.
- Explicit limits: `max_routes` default 1200, `max_points` default 120000. The metadata's `truncated` flag is **required** to distinguish incomplete responses from complete coverage.
- Original `shape_id` geometry is tagged `quality=source-shape`; missing/invalid shapes are approximated with representative stop-to-stop links tagged `quality=schematic-stop-link`. A schematic line is not a real road/rail alignment.
- De-duplicated shapes within each source-scoped route; different feeds with the same raw route ID remain separate.
- New route-specific `GET /api/v1/route/{snapshot}/{route}/timetable?date=YYYY-MM-DD&limit=100` query provides departure times on a **GTFS service day**, direction IDs and first/last stops. Frequency-only schedules do **not** create fictitious departures.
- MapLibre frontend loads the complete network and stops simultaneously, allows clicking routes and stops, filters by operator/source, date, and transport mode, and distinguishes online street basemap and plain offline mode.
- Street basemap provider: [OpenFreeMap Liberty](https://openfreemap.org/quick_start/). Online tiles require an internet connection and are subject to provider conditions. Plain mode uses no external tiles. No offline basemap caching has been implemented.

## Known limitations

- Rendering scales to the response caps, not arbitrarily large countries. Geometry bounding-box tile queries and server-side simplification are future work. The atlas must visibly warn when capped.
- Map source geometry represents route-shape variants, **not** a perfect set of physical infrastructure edges or a walkable public transport graph.
- At most one representative schematic missing-shape trip per route is shown; other route variants may exist. More precise pattern-specific fallback and completeness accounting are future work.
- First/last-stop route timetable is a timetable summary, not a full journey planner. Stop boards show wall dates and may include trips from previous GTFS service dates.
- `direction_id=0/1` is an operator-defined categorical direction; it does not imply compass direction.
- Stops API currently truncates at 20,000 records. Mode/source filters apply to routes but not stop markers, so stop coverage may be broader than the selected routes.
- Real ARST/ATP/Trenitalia/ASPO imports have not been run in this implementation environment. The synthetic demo remains the only validated data here.
- No automatic feed download is included. Check original feed validity, license and attribution before importing or redistributing.

## Local usage

After updating the repository, stop both running PowerShell processes with Ctrl+C and restart them:

```powershell
cd C:\Users\User\TransportAtlas
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m transport_atlas serve
```

In a separate terminal:

```powershell
cd C:\Users\User\TransportAtlas\frontend
npm install
npm run dev
```

Open http://127.0.0.1:5173. Your previously imported demo database remains in place. Internet access is needed for the optional default OpenFreeMap basemap, not for the local API or GTFS data.

## Next gates

1. Frontend CI build succeeds; user opens the local app and confirms route highlighting plus stop inspector.
2. Import the official ARST dataset and run an external GTFS validator (MobilityData recommended). Record source URL, download date, licensing, and service validity.
3. Profile the `/network/geometry` query with real feed sizes, add bounding-box viewport filtering/vector tiling, and address any performance bottlenecks before multi-country use.
