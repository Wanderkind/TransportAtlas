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
