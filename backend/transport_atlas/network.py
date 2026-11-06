"""Bounded geographic network export for the research map.

This is intentionally a *route atlas*, not a journey-planning graph: route shapes
can represent many trip patterns, and straight stop links are explicitly schematic.
"""
from __future__ import annotations

from datetime import date
from collections import defaultdict

from .storage import rows
from .schedule import active_services, active_snapshots
from .provenance import declared_bounds

SHAPE_CACHE_LIMIT = 8192

COVERAGE_KEYS = ('total_route_records','routes_with_trip_references','active_routes',
                 'eligible_routes','rendered_routes','routes_without_trip_references',
                 'routes_with_insufficient_geometry','routes_omitted_by_limits',
                 'inactive_routes_excluded','partially_rendered_routes')

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
    """Douglas–Peucker in coordinate degrees; preserve endpoints and major bends.

    A radial pass first removes dense samples within half the tolerance; the
    remaining half bounds the Douglas–Peucker deviation. Coordinates are read
    from flat lists and the projection clamp is branched, which returns exactly
    the same points as the original min/max formulation with fewer calls.
    """
    if tolerance <= 0 or len(points) <= 2:
        return points
    threshold = (tolerance/2)**2
    last = points[0]
    lx, ly = last[0], last[1]
    radial = [last]
    append = radial.append
    for index in range(1, len(points)-1):
        point = points[index]
        x, y = point[0], point[1]
        if (x-lx)**2+(y-ly)**2 >= threshold:
            append(point)
            lx, ly = x, y
    append(points[-1])
    points = radial
    tolerance /= 2
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    keep = [False]*len(points)
    keep[0] = keep[-1] = True
    limit = tolerance*tolerance
    stack = [(0, len(points)-1)]
    pop, push = stack.pop, stack.append
    while stack:
        start, end = pop()
        ax, ay = xs[start], ys[start]
        dx, dy = xs[end]-ax, ys[end]-ay
        length = dx*dx+dy*dy
        greatest, index = limit, -1
        if length:
            for i in range(start+1, end):
                px = xs[i]-ax
                py = ys[i]-ay
                t = (px*dx+py*dy)/length
                if t <= 0:
                    distance = px**2+py**2
                elif t >= 1:
                    distance = (px-dx)**2+(py-dy)**2
                else:
                    distance = (px-t*dx)**2+(py-t*dy)**2
                if distance > greatest:
                    greatest, index = distance, i
        else:
            for i in range(start+1, end):
                px = xs[i]-ax
                py = ys[i]-ay
                distance = px**2+py**2
                if distance > greatest:
                    greatest, index = distance, i
        if index >= 0:
            keep[index] = True
            push((start, index))
            push((index, end))
    return [p for p, k in zip(points, keep) if k]


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
    remaining_routes = max_routes
    used_points = 0
    original_points = 0
    truncated = False
    source_count = 0
    missing_geometries = 0
    snap_details = []
    for snapshot in active_snapshots(con):
        if source_id and snapshot['source_id'] != source_id:
            continue
        if source_ids is not None and snapshot['source_id'] not in source_ids:
            continue
        source_count += 1
        sid = snapshot['snapshot_id']
        day_services = active_services(con, sid, service_day)
        services = day_services if active_only else None
        declared_start, declared_end = declared_bounds(snapshot)
        feed_covered = (snapshot['feed_start'] <= service_day.isoformat() <= snapshot['feed_end']
                        and (not declared_start or service_day.isoformat()>=declared_start)
                        and (not declared_end or service_day.isoformat()<=declared_end))
        candidates = rows(con, 'SELECT route_id, route_type FROM routes WHERE snapshot_id=? ORDER BY route_id', (sid,))
        matched = [r for r in candidates if MODE_TYPES[mode] is None or r['route_type'] in MODE_TYPES[mode]]
        trips = rows(con, 'SELECT trip_id,route_id,service_id,shape_id,direction_id FROM trips WHERE snapshot_id=? ORDER BY trip_id', (sid,))
        matched_ids = {r['route_id'] for r in matched}
        referenced = {t['route_id'] for t in trips} & matched_ids
        active = {t['route_id'] for t in trips if t['service_id'] in day_services} & matched_ids
        coverage = dict.fromkeys(COVERAGE_KEYS,0)
        coverage.update(total_route_records=len(matched), routes_with_trip_references=len(referenced),
                        active_routes=len(active),routes_without_trip_references=len(matched_ids-referenced),
                        inactive_routes_excluded=len(referenced-active) if active_only else 0)
        detail = dict(source_id=snapshot['source_id'],snapshot_id=sid,feed_covered=feed_covered,routes=0,coverage=coverage)
        snap_details.append(detail)
        selected_trips = [t for t in trips if t['route_id'] in matched_ids and
                          (services is None or t['service_id'] in services)]
        trips_by_route: dict[str, list[dict]] = defaultdict(list)
        for trip in selected_trips:
            trips_by_route[trip['route_id']].append(trip)
        eligible = [r for r in matched if trips_by_route.get(r['route_id'])]
        coverage['eligible_routes'] = len(eligible)
        # Determine data adequacy BEFORE caps, without fetching all vertices.
        good_shape_ids = {r['shape_id'] for r in rows(con, '''SELECT shape_id FROM shapes
            WHERE snapshot_id=? GROUP BY shape_id HAVING COUNT(*)>=2''',(sid,))} if eligible else set()
        good_stop_trips = {r['trip_id'] for r in rows(con, '''SELECT st.trip_id FROM stop_times st
            JOIN stops s ON s.snapshot_id=st.snapshot_id AND s.stop_id=st.stop_id
            WHERE st.snapshot_id=? AND s.lat IS NOT NULL AND s.lon IS NOT NULL
            GROUP BY st.trip_id HAVING COUNT(*)>=2''',(sid,))} if eligible else set()
        drawable = {t['route_id'] for t in selected_trips if t['shape_id'] in good_shape_ids or t['trip_id'] in good_stop_trips}
        coverage['routes_with_insufficient_geometry'] = len(eligible)-len(drawable)
        eligible = [r for r in eligible if r['route_id'] in drawable]
        if len(eligible) > remaining_routes:
            truncated = True
            eligible = eligible[:max(0, remaining_routes)]
        remaining_routes -= len(eligible)
        if not eligible:
            continue

        by_route = {r['route_id']: r for r in eligible}
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
                if len(shape_cache) >= SHAPE_CACHE_LIMIT:
                    shape_cache.pop(next(iter(shape_cache)))
                shape_cache[(sid, shape)] = (original_count, shape_points[shape], {})
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
    before_limits = defaultdict(int)
    for f in features:
        before_limits[(f['properties']['snapshot_id'],f['properties']['route_id'])] += 1
    tolerance = 0.0001
    minimum_points = sum(2 if f['properties']['quality']=='source-shape' else len(f['geometry']['coordinates']) for f in features)
    while used_points > max_points and tolerance < 0.05:
        tolerance *= 2
        used_points = 0
        for feature, original in zip(features, originals):
            properties = feature['properties']
            if properties['quality']=='source-shape':
                coords = _coarser(shape_cache, properties['snapshot_id'], properties['shape_id'], original, tolerance)
            else:
                coords = original
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
    after_limits = defaultdict(int)
    for f in features:
        after_limits[(f['properties']['snapshot_id'],f['properties']['route_id'])] += 1
    for detail in snap_details:
        sid=detail['snapshot_id']
        c=detail['coverage']
        c['rendered_routes']=sum(key[0]==sid for key in drawn_keys)
        c['routes_omitted_by_limits']=c['eligible_routes']-c['routes_with_insufficient_geometry']-c['rendered_routes']
        c['partially_rendered_routes']=sum(key[0]==sid and after_limits[key]<before_limits[key] for key in drawn_keys)
        detail['routes']=c['rendered_routes']
    coverage={key:sum(s['coverage'][key] for s in snap_details) for key in COVERAGE_KEYS}
    missing_geometries=coverage['routes_without_trip_references']+coverage['routes_with_insufficient_geometry']
    warnings=[]
    if coverage['routes_without_trip_references']:
        warnings.append(f"{coverage['routes_without_trip_references']} imported route records have no trip references; no alignment is inferred. This does not establish absence of service.")
    if coverage['routes_with_insufficient_geometry']:
        warnings.append(f"{coverage['routes_with_insufficient_geometry']} eligible routes lack sufficient shape/stop coordinates.")
    if truncated:
        warnings.append('Display limit reached; routes or variants are omitted. Narrow the filters.')

    return {'type': 'FeatureCollection', 'features': features,
            'metadata': {'service_date': service_day.isoformat(), 'active_only': active_only,
                         'mode': mode, 'source_filter': source_id, 'source_count': source_count,
                         'route_count': coverage['rendered_routes'], 'feature_count': len(features),
                         'coverage':coverage,
                         'coverage_definition': 'T=N+inactive_excluded+eligible; eligible=rendered+insufficient+limit_omitted. In active-only mode eligible=active; otherwise eligible=trip_referenced. Partially rendered is a subset of rendered.',
                         'filters':{'date':service_day.isoformat(),'mode':mode,'source':source_id,
                                    'sources':sorted(source_ids) if source_ids is not None else None,'active_only':active_only},
                         'point_count': used_points, 'max_routes': max_routes,
                         'original_shape_points': original_points,
                         'simplification_tolerance_degrees': tolerance + (0.0001 if tolerance>0.0001 else 0),
                         'max_points': max_points, 'truncated': truncated,
                         'missing_geometry_routes': missing_geometries,
                         'snapshots': snap_details,
                         'warning': ' '.join(warnings) if warnings else None}}


def _coarser(shape_cache, snapshot_id, shape_id, base, tolerance):
    """Re-simplify a base-tolerance shape, memoized per immutable snapshot shape.

    Snapshot shapes never change, so the coarser display levels chosen by the
    point budget are reusable across dates and filters within a process.
    """
    if shape_cache is None or shape_id is None:
        return simplify_line(base, tolerance)
    entry = shape_cache.get((snapshot_id, shape_id))
    if entry is None or entry[1] is not base:
        return simplify_line(base, tolerance)
    levels = entry[2]
    coords = levels.get(tolerance)
    if coords is None:
        coords = levels[tolerance] = simplify_line(base, tolerance)
    return coords


def _feature(snapshot_id, source_id, route_id, route_type, mode, coordinates, quality, shape_id):
    return {'type': 'Feature',
            'properties': {'snapshot_id': snapshot_id, 'source_id': source_id,
                           'route_id': route_id, 'uid': snapshot_id + ':' + route_id,
                           'route_type': route_type, 'mode': mode, 'color': COLORS[mode],
                           'quality': quality, 'shape_id': shape_id},
            'geometry': {'type': 'LineString', 'coordinates': coordinates}}


def _direction(value):
    return 'null' if value is None else str(value)
