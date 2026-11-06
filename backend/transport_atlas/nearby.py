"""Cross-source stop proximity for the inspector.

This reports stops within a straight-line radius, across all selected source
snapshots. It is a proximity aid, not physical-stop matching: separate source
records near each other may be different platforms, and no walking route,
accessibility or transfer guarantee is implied.
"""
from __future__ import annotations
import math
from .schedule import active_snapshots
from .storage import one, rows

EARTH_RADIUS_M = 6_371_008.8
NOTE = ('Straight-line proximity between source stop records. Not a verified '
        'physical-stop match, walking route or guaranteed transfer.')


def distance_m(lat1, lon1, lat2, lon2):
    """Great-circle (haversine) distance in metres."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2-p1, math.radians(lon2-lon1)
    a = math.sin(dp/2)**2+math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2*EARTH_RADIUS_M*math.asin(min(1.0, math.sqrt(a)))


def nearby_stops(con, snapshot_id: str, stop_id: str, *, radius_m: float = 200, limit: int = 30) -> dict:
    origin = one(con, 'SELECT * FROM stops WHERE snapshot_id=? AND stop_id=?', (snapshot_id, stop_id))
    if origin is None:
        raise KeyError('Unknown stop')
    result = {'origin': {'snapshot_id': snapshot_id, 'stop_id': stop_id, 'name': origin['name']},
              'radius_m': radius_m, 'stops': [], 'total': 0, 'truncated': False, 'note': NOTE}
    lat, lon = origin['lat'], origin['lon']
    if lat is None or lon is None:
        result['note'] = 'This stop record has no coordinates; proximity is unavailable.'
        return result
    # Bounding-box prefilter in SQL, exact haversine in Python.
    dlat = math.degrees(radius_m/EARTH_RADIUS_M)
    dlon = dlat/max(math.cos(math.radians(lat)), 1e-6)
    found = []
    for snapshot in active_snapshots(con):
        for stop in rows(con, '''SELECT snapshot_id,stop_id,name,lat,lon FROM stops
                WHERE snapshot_id=? AND lat BETWEEN ? AND ? AND lon BETWEEN ? AND ?''',
                (snapshot['snapshot_id'], lat-dlat, lat+dlat, lon-dlon, lon+dlon)):
            if stop['snapshot_id'] == snapshot_id and stop['stop_id'] == stop_id:
                continue
            distance = distance_m(lat, lon, stop['lat'], stop['lon'])
            if distance <= radius_m:
                found.append(dict(stop, source_id=snapshot['source_id'], distance_m=round(distance, 1),
                                  same_source=snapshot['snapshot_id'] == snapshot_id))
    found.sort(key=lambda s: (s['distance_m'], s['source_id'], s['stop_id']))
    result.update(stops=found[:limit], total=len(found), truncated=len(found) > limit)
    return result


def stop_routes(con, snapshot_id: str, stop_id: str, service_day) -> dict:
    """Routes whose trips call at a stop, with calls on the selected service day.

    Counts are trip templates calling at the stop (a trip visiting twice counts
    once). ``calls_on_date`` uses the GTFS service day; frequency-based trips are
    counted as templates, not expanded instances.
    """
    from .network import mode_for_type
    from .schedule import active_services
    stop = one(con, 'SELECT stop_id FROM stops WHERE snapshot_id=? AND stop_id=?', (snapshot_id, stop_id))
    if stop is None:
        raise KeyError('Unknown stop')
    services = active_services(con, snapshot_id, service_day)
    calls = rows(con, '''SELECT DISTINCT t.trip_id,t.route_id,t.service_id FROM stop_times st
        JOIN trips t ON t.snapshot_id=st.snapshot_id AND t.trip_id=st.trip_id
        WHERE st.snapshot_id=? AND st.stop_id=?''', (snapshot_id, stop_id))
    counts: dict[str, list[int]] = {}
    for call in calls:
        total_active = counts.setdefault(call['route_id'], [0, 0])
        total_active[0] += 1
        total_active[1] += call['service_id'] in services
    routes = []
    if counts:
        ids = sorted(counts)
        placeholders = ','.join('?' for _ in ids)
        for route in rows(con, f'''SELECT route_id,short_name,long_name,route_type FROM routes
                WHERE snapshot_id=? AND route_id IN ({placeholders})''', (snapshot_id, *ids)):
            total, active = counts[route['route_id']]
            routes.append(dict(route, snapshot_id=snapshot_id, mode=mode_for_type(route['route_type']),
                               trip_templates=total, calls_on_date=active))
    routes.sort(key=lambda r: (-r['calls_on_date'], r['short_name'] or r['route_id'], r['route_id']))
    return {'snapshot_id': snapshot_id, 'stop_id': stop_id, 'service_date': str(service_day), 'routes': routes,
            'note': 'Trip templates calling at this stop; frequency trips are not expanded.'}
