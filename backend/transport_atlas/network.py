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
    'rail': {1, 2, 5, 12},
    'tram': {0},
    'ferry': {4},
}

COLORS = {
    'bus': '#3583be',
    'rail': '#b36b9e',
    'tram': '#da7933',
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


def simplify_line(points, tolerance=0.0001):
    """Douglas–Peucker in coordinate degrees; preserve endpoints and major bends."""
    if tolerance <= 0 or len(points) <= 2:
        return points
    # A linear radial pass removes dense samples before the more costly DP pass.
    # Split the error budget so the combined deviation stays within tolerance.
    radial = [points[0]]
    threshold = (tolerance/2)**2
    for point in points[1:-1]:
        if (point[0]-radial[-1][0])**2+(point[1]-radial[-1][1])**2 >= threshold:
            radial.append(point)
    radial.append(points[-1])
    points = radial
    tolerance /= 2
    keep = {0, len(points)-1}
    stack = [(0, len(points)-1)]
    while stack:
        start, end = stack.pop()
        ax, ay = points[start]
        bx, by = points[end]
        dx, dy = bx-ax, by-ay
        length = dx*dx+dy*dy
        greatest, index = tolerance*tolerance, None
        for i in range(start+1, end):
            px, py = points[i]
            t = max(0, min(1, ((px-ax)*dx+(py-ay)*dy)/length)) if length else 0
            distance = (px-ax-t*dx)**2+(py-ay-t*dy)**2
            if distance > greatest:
                greatest, index = distance, i
        if index is not None:
            keep.add(index)
            stack.extend(((start, index), (index, end)))
    return [points[i] for i in sorted(keep)]


def network_geometry(con, service_day: date, *, source_id: str | None = None,
                     source_ids: set[str] | None = None,
                     shape_cache: dict | None = None,
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
    original_points = 0
    truncated = False
    source_count = 0
    missing_geometries = 0
    snap_details = []
    selected_keys = set()
    for snapshot in active_snapshots(con):
        if source_id and snapshot['source_id'] != source_id:
            continue
        if source_ids is not None and snapshot['source_id'] not in source_ids:
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

        trips = rows(con, 'SELECT trip_id,route_id,service_id,shape_id,direction_id FROM trips WHERE snapshot_id=? ORDER BY trip_id', (sid,))
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
        selected_keys.update((sid, rid) for rid in by_route)
        shapes_needed = sorted({t['shape_id'] for r in eligible for t in trips_by_route[r['route_id']] if t['shape_id']})
        shape_points: dict[str, list[list[float]]] = defaultdict(list)
        missing_shapes = []
        for shape in shapes_needed:
            cached = shape_cache.get((sid, shape)) if shape_cache is not None else None
            if cached is not None:
                original_points += cached[0]
                shape_points[shape] = cached[1]
            else:
                missing_shapes.append(shape)
        for group in _chunks(missing_shapes):
            placeholders = ','.join('?' for _ in group)
            for p in rows(con, f'''SELECT shape_id,lon,lat FROM shapes
                WHERE snapshot_id=? AND shape_id IN ({placeholders})
                ORDER BY shape_id,point_sequence''', (sid, *group)):
                shape_points[p['shape_id']].append([p['lon'],p['lat']])

        # Source shape simplification bounds payload size before applying the cap.
        for shape in missing_shapes:
            original_count = len(shape_points[shape])
            original_points += original_count
            shape_points[shape] = simplify_line(shape_points[shape])
            if shape_cache is not None:
                if len(shape_cache) >= 8192:
                    shape_cache.pop(next(iter(shape_cache)))
                shape_cache[(sid, shape)] = (original_count, shape_points[shape])
        # Preserve every distinct schematic stop sequence, not just one per route.
        # Multiple trips with the same shape are never rendered repeatedly.
        schematic_trip_ids: set[str] = set()
        for rid, route in by_route.items():
            route_trips = trips_by_route[rid]
            good_shapes = set()
            for trip in route_trips:
                shape = trip['shape_id']
                if shape and len(shape_points.get(shape, [])) >= 2:
                    good_shapes.add(shape)
                else:
                    schematic_trip_ids.add(trip['trip_id'])
            m = mode_for_type(route['route_type'])
            for shape_id in sorted(good_shapes):
                coordinates = shape_points[shape_id]
                feature = _feature(sid, snapshot['source_id'], rid, route['route_type'],
                                   m, coordinates, 'source-shape', shape_id)
                feature['properties']['direction_ids'] = sorted({_direction(t['direction_id']) for t in route_trips if t['shape_id'] == shape_id})
                features.append(feature)
                used_points += len(coordinates)

        if schematic_trip_ids:
            schematic_seen = {}
            trip_directions = {t['trip_id']: _direction(t['direction_id']) for rid in by_route for t in trips_by_route[rid]}
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
                    rid = trip_routes[trip_id]
                    signature = (rid, tuple(tuple(p) for p in coords))
                    if signature in schematic_seen:
                        directions = schematic_seen[signature]['properties']['direction_ids']
                        if trip_directions[trip_id] not in directions:
                            directions.append(trip_directions[trip_id])
                        continue
                    if len(coords) < 2:
                        continue
                    route = by_route[rid]
                    feature = _feature(sid, snapshot['source_id'], rid,
                                       route['route_type'], mode_for_type(route['route_type']),
                                       coords, 'schematic-stop-link', None)
                    feature['properties']['direction_ids'] = [trip_directions[trip_id]]
                    schematic_seen[signature] = feature
                    features.append(feature)
                    used_points += len(coords)
    # Allocate the display budget across ALL sources, rather than allowing the
    # first operator to consume it before later operators get any alignments.
    originals = [f['geometry']['coordinates'] for f in features]
    tolerance = 0.0001
    minimum_points = sum(2 if f['properties']['quality']=='source-shape' else len(f['geometry']['coordinates']) for f in features)
    while used_points > max_points and tolerance < 0.05:
        tolerance *= 2
        used_points = 0
        for feature, original in zip(features, originals):
            coords = simplify_line(original, tolerance) if feature['properties']['quality']=='source-shape' else original
            feature['geometry']['coordinates'] = coords
            used_points += len(coords)
        if used_points == minimum_points:
            break
    if used_points > max_points:
        truncated = True
        # Prefer one alignment per route before additional variants when the
        # budget is too small even for simplified lines.
        first, additional, seen = [], [], set()
        for feature in features:
            uid = feature['properties']['uid']
            (additional if uid in seen else first).append(feature)
            seen.add(uid)
        features = []
        used_points = 0
        for feature in first + additional:
            count = len(feature['geometry']['coordinates'])
            if used_points+count <= max_points:
                features.append(feature)
                used_points += count
    drawn_keys = {(f['properties']['snapshot_id'],f['properties']['route_id']) for f in features}
    missing_geometries = len(selected_keys - drawn_keys)

    return {'type': 'FeatureCollection', 'features': features,
            'metadata': {'service_date': service_day.isoformat(), 'active_only': active_only,
                         'mode': mode, 'source_filter': source_id, 'source_count': source_count,
                         'route_count': selected_routes, 'feature_count': len(features),
                         'point_count': used_points, 'max_routes': max_routes,
                         'original_shape_points': original_points,
                         'simplification_tolerance_degrees': tolerance + (0.0001 if tolerance>0.0001 else 0),
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


def _direction(value):
    return 'null' if value is None else str(value)
