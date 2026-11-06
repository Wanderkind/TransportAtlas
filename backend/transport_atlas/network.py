"""Bounded geographic network export for the research map.

This is intentionally a *route atlas*, not a journey-planning graph: route shapes
can represent many trip patterns, and straight stop links are explicitly schematic.
"""
from __future__ import annotations

from datetime import date
from collections import defaultdict

from .storage import rows
from .schedule import active_services, active_snapshots

MODE_TYPES = {
    'all': None,
    'bus': {3, 11},
    'rail': {0, 1, 2, 5, 12},
    'ferry': {4},
}

COLORS = {
    'bus': '#3583be',
    'rail': '#b36b9e',
    'ferry': '#229a90',
    'other': '#bd863b',
}


def mode_for_type(route_type: int) -> str:
    for name, values in MODE_TYPES.items():
        if values is not None and route_type in values:
            return name
    return 'other'


def _chunks(items, size=250):
    for i in range(0, len(items), size):
        yield items[i:i+size]


def network_geometry(con, service_day: date, *, source_id: str | None = None,
                     mode: str = 'all', active_only: bool = True,
                     max_routes: int = 1200, max_points: int = 120000) -> dict:
    """One bounded FeatureCollection; response metadata warns about truncation.

    * Filters apply before geometry lookup.
    * Shape points are fetched in batches, not one query per trip.
    * A shared shape used by different routes stays separately addressable.
    * If a trip lacks a valid shape, its stop links are marked schematic.
    * No routes or points may be silently cut off.
    """
    if mode not in MODE_TYPES:
        raise ValueError(f'Unknown mode: {mode}')
    features: list[dict] = []
    selected_routes = 0
    remaining_routes = max_routes
    used_points = 0
    truncated = False
    source_count = 0
    missing_geometries = 0
    snap_details = []
    for snapshot in active_snapshots(con):
        if source_id and snapshot['source_id'] != source_id:
            continue
        source_count += 1
        sid = snapshot['snapshot_id']
        services = active_services(con, sid, service_day) if active_only else None
        feed_covered = snapshot['feed_start'] <= service_day.isoformat() <= snapshot['feed_end']
        candidates = rows(con, 'SELECT route_id, route_type FROM routes WHERE snapshot_id=? ORDER BY route_id', (sid,))
        matched = [r for r in candidates if MODE_TYPES[mode] is None or r['route_type'] in MODE_TYPES[mode]]
        if not matched:
            snap_details.append(dict(source_id=snapshot['source_id'], feed_covered=feed_covered, routes=0))
            continue

        trips = rows(con, 'SELECT trip_id,route_id,service_id,shape_id FROM trips WHERE snapshot_id=? ORDER BY trip_id', (sid,))
        matched_ids = {r['route_id'] for r in matched}
        selected_trips = [t for t in trips if t['route_id'] in matched_ids and
                          (services is None or t['service_id'] in services)]
        trips_by_route: dict[str, list[dict]] = defaultdict(list)
        for trip in selected_trips:
            trips_by_route[trip['route_id']].append(trip)
        eligible = [r for r in matched if trips_by_route.get(r['route_id'])]
        if len(eligible) > remaining_routes:
            truncated = True
            eligible = eligible[:max(0, remaining_routes)]
        selected_routes += len(eligible)
        remaining_routes -= len(eligible)
        snap_details.append(dict(source_id=snapshot['source_id'], feed_covered=feed_covered, routes=len(eligible)))
        if not eligible:
            continue

        by_route = {r['route_id']: r for r in eligible}
        shapes_needed = sorted({t['shape_id'] for r in eligible for t in trips_by_route[r['route_id']] if t['shape_id']})
        shape_points: dict[str, list[list[float]]] = defaultdict(list)
        for group in _chunks(shapes_needed):
            placeholders = ','.join('?' for _ in group)
            for p in rows(con, f'''SELECT shape_id,lon,lat FROM shapes
                WHERE snapshot_id=? AND shape_id IN ({placeholders})
                ORDER BY shape_id,point_sequence''', (sid, *group)):
                shape_points[p['shape_id']].append([p['lon'],p['lat']])

        # Find a single representative schematic for each distinct sequence of stops.
        # Multiple trips with the same shape are never rendered repeatedly.
        schematic_trip_ids: set[str] = set()
        for rid, route in by_route.items():
            route_trips = trips_by_route[rid]
            good_shapes = set()
            missing_shape_trip = None
            for trip in route_trips:
                shape = trip['shape_id']
                if shape and len(shape_points.get(shape, [])) >= 2:
                    good_shapes.add(shape)
                elif missing_shape_trip is None:
                    missing_shape_trip = trip['trip_id']
            if missing_shape_trip:
                schematic_trip_ids.add(missing_shape_trip)
            m = mode_for_type(route['route_type'])
            for shape_id in sorted(good_shapes):
                coordinates = shape_points[shape_id]
                if used_points + len(coordinates) > max_points:
                    truncated = True
                    continue
                features.append(_feature(sid, snapshot['source_id'], rid, route['route_type'],
                                         m, coordinates, 'source-shape', shape_id))
                used_points += len(coordinates)

        if schematic_trip_ids:
            # One indexed scan of stop_times for the required trips instead of one query per stop.
            for group in _chunks(sorted(schematic_trip_ids)):
                placeholders = ','.join('?' for _ in group)
                stop_rows = rows(con, f'''SELECT st.trip_id,s.lon,s.lat FROM stop_times st
                    JOIN stops s ON s.snapshot_id=st.snapshot_id AND s.stop_id=st.stop_id
                    WHERE st.snapshot_id=? AND st.trip_id IN ({placeholders})
                    AND s.lat IS NOT NULL AND s.lon IS NOT NULL
                    ORDER BY st.trip_id,st.stop_sequence''', (sid, *group))
                coords_by_trip: dict[str, list[list[float]]] = defaultdict(list)
                for stop in stop_rows:
                    coords_by_trip[stop['trip_id']].append([stop['lon'], stop['lat']])
                trip_routes = {t['trip_id']: t['route_id'] for rid in by_route for t in trips_by_route[rid]
                               if t['trip_id'] in schematic_trip_ids}
                for trip_id, coords in coords_by_trip.items():
                    if len(coords) < 2:
                        missing_geometries += 1
                        continue
                    if used_points + len(coords) > max_points:
                        truncated = True
                        continue
                    rid = trip_routes[trip_id]
                    route = by_route[rid]
                    features.append(_feature(sid, snapshot['source_id'], rid,
                                             route['route_type'], mode_for_type(route['route_type']),
                                             coords, 'schematic-stop-link', None))
                    used_points += len(coords)
        missing_geometries += sum(1 for r in eligible if not any(f['properties']['route_id'] == r['route_id']
                                                                  and f['properties']['snapshot_id'] == sid for f in features))

    return {'type': 'FeatureCollection', 'features': features,
            'metadata': {'service_date': service_day.isoformat(), 'active_only': active_only,
                         'mode': mode, 'source_filter': source_id, 'source_count': source_count,
                         'route_count': selected_routes, 'feature_count': len(features),
                         'point_count': used_points, 'max_routes': max_routes,
                         'max_points': max_points, 'truncated': truncated,
                         'missing_geometry_routes': missing_geometries,
                         'snapshots': snap_details,
                         'warning': ('Geometry limit reached; network incomplete. Narrow the filters.'
                                     if truncated else None)}}


def _feature(snapshot_id, source_id, route_id, route_type, mode, coordinates, quality, shape_id):
    return {'type': 'Feature',
            'properties': {'snapshot_id': snapshot_id, 'source_id': source_id,
                           'route_id': route_id, 'uid': snapshot_id + ':' + route_id,
                           'route_type': route_type, 'mode': mode, 'color': COLORS[mode],
                           'quality': quality, 'shape_id': shape_id},
            'geometry': {'type': 'LineString', 'coordinates': coordinates}}
