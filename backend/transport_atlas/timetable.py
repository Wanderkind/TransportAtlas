"""Route timetable for a GTFS *service date* (not a wall-clock departure board)."""
from __future__ import annotations
from datetime import date
from .schedule import active_services, gtfs_instant
from .storage import one, rows
from .route_details import route_variants, route_evidence
from statistics import median


def route_timetable(con, snapshot_id: str, route_id: str, service_date: date, *, limit: int = 100) -> dict:
    route = one(con, 'SELECT * FROM routes WHERE snapshot_id=? AND route_id=?', (snapshot_id,route_id))
    if route is None:
        raise KeyError('Unknown route')
    services = active_services(con, snapshot_id, service_date)
    variants, trip_variants = route_variants(con, snapshot_id, route_id)
    trips = rows(con, '''SELECT t.trip_id,t.service_id,t.headsign,t.direction_id,
         a.timezone AS agency_timezone, fst.stop_id AS first_stop_id,
         fs.name AS first_stop_name, COALESCE(fst.departure_sec,fst.arrival_sec) AS first_sec,
         lst.stop_id AS last_stop_id, ls.name AS last_stop_name,
         COALESCE(lst.arrival_sec,lst.departure_sec) AS last_sec
        FROM trips t
        JOIN routes r ON r.snapshot_id=t.snapshot_id AND r.route_id=t.route_id
        JOIN agencies a ON a.snapshot_id=t.snapshot_id AND a.agency_id=r.agency_id
        LEFT JOIN stop_times fst ON fst.snapshot_id=t.snapshot_id AND fst.trip_id=t.trip_id
         AND fst.stop_sequence=(SELECT MIN(x.stop_sequence) FROM stop_times x
                               WHERE x.snapshot_id=t.snapshot_id AND x.trip_id=t.trip_id)
        LEFT JOIN stops fs ON fs.snapshot_id=fst.snapshot_id AND fs.stop_id=fst.stop_id
        LEFT JOIN stop_times lst ON lst.snapshot_id=t.snapshot_id AND lst.trip_id=t.trip_id
         AND lst.stop_sequence=(SELECT MAX(x.stop_sequence) FROM stop_times x
                               WHERE x.snapshot_id=t.snapshot_id AND x.trip_id=t.trip_id)
        LEFT JOIN stops ls ON ls.snapshot_id=lst.snapshot_id AND ls.stop_id=lst.stop_id
        WHERE t.snapshot_id=? AND t.route_id=?
        ORDER BY t.trip_id''', (snapshot_id,route_id))
    active = [t for t in trips if t['service_id'] in services]
    frequency_rows = rows(con, '''SELECT f.* FROM frequencies f JOIN trips t
        ON t.snapshot_id=f.snapshot_id AND t.trip_id=f.trip_id
        WHERE t.snapshot_id=? AND t.route_id=? ORDER BY f.trip_id,f.start_sec''',
        (snapshot_id,route_id))
    by_trip: dict[str,list[dict]] = {}
    for f in frequency_rows:
        by_trip.setdefault(f['trip_id'],[]).append(f)

    departures=[]
    windows=[]
    for t in active:
        if t['first_sec'] is None:
            continue
        freqs=by_trip.get(t['trip_id'],[])
        seconds = [t['first_sec']] if not freqs else []
        for f in freqs:
            if f['exact_times'] == 1:
                seconds.extend(range(f['start_sec'],f['end_sec'],f['headway_secs']))
            else:
                tz=t['agency_timezone']
                windows.append({'trip_id':t['trip_id'],'direction_id':t['direction_id'],
                    'variant_id':trip_variants[t['trip_id']],
                    'headsign':t['headsign'],'first_stop_name':t['first_stop_name'],
                    'start_local':gtfs_instant(service_date,f['start_sec'],tz).isoformat(),
                    'end_local':gtfs_instant(service_date,f['end_sec'],tz).isoformat(),
                    'headway_seconds':f['headway_secs'],'precision':'frequency_not_exact'})
        for sec in seconds:
            tz=t['agency_timezone']
            arrival = sec + (t['last_sec']-t['first_sec']) if t['last_sec'] is not None else None
            departures.append({'trip_id':t['trip_id'],'direction_id':t['direction_id'],
               'variant_id':trip_variants[t['trip_id']], 'departure_seconds':sec,
               'template_first_seconds':t['first_sec'], 'service_id':t['service_id'],
               'headsign':t['headsign'],'first_stop_id':t['first_stop_id'],
               'first_stop_name':t['first_stop_name'],'last_stop_name':t['last_stop_name'],
               'departure_local':gtfs_instant(service_date,sec,tz).isoformat(),
               'arrival_local':gtfs_instant(service_date,arrival,tz).isoformat() if arrival is not None else None,
               'precision':'scheduled'})
    departures.sort(key=lambda item:(item['departure_local'],item['trip_id']))
    windows.sort(key=lambda item:(item['start_local'],item['trip_id']))
    for variant in variants:
        precise = [d for d in departures if d['variant_id'] == variant['variant_id']]
        seconds = [d['departure_seconds'] for d in precise]
        variant.update(scheduled_departures=len(precise),
                       first_departure=precise[0]['departure_local'] if precise else None,
                       last_departure=precise[-1]['departure_local'] if precise else None,
                       median_interval_minutes=median([b-a for a,b in zip(seconds, seconds[1:])])/60 if len(seconds)>1 else None,
                       frequency_windows=sum(w['variant_id']==variant['variant_id'] for w in windows))
    return {'snapshot_id':snapshot_id,'route_id':route_id,'service_date':service_date.isoformat(),
            'first_departure':departures[0]['departure_local'] if departures else None,
            'last_departure':departures[-1]['departure_local'] if departures else None,
            'variants':variants, **route_evidence(con, snapshot_id, route_id),
            'total_departures':len(departures),'departures':departures[:limit],
            'truncated':len(departures)>limit,'frequency_windows':windows[:limit],
            'windows_truncated':len(windows)>limit,
            'note':'Only precise scheduled times appear as departures. Non-exact headways remain windows.'}
