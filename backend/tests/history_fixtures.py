"""Synthetic two-version GTFS feeds of one fictional operator, plus a second
operator that reuses its route and stop IDs. Entirely invented: these must never
be presented as observed history of any real network.

Version 1 (feed_version "2026.09", calendars 2026-09-01..2026-12-31)
Version 2 (feed_version "2026.11", calendars 2026-11-01..2027-03-31)

Designed differences (by GTFS ID within source ``synthetic_valley``):

* R1   unchanged route; 2 weekday trips in both; a Saturday trip in both.
* R2   same ID, long name and headsign changed; 2 -> 3 weekday trips.
* R9   only in v1 (removed record).      R10 only in v2 (added record).
* N1   overnight trip 23:40 -> 24:20 in both.
* FX   exact_times=1 frequency 08:00-09:00 / 20 min (3 departures) in both.
* FW   exact_times=0 headway window 10:00-11:00 / 15 min in both.
* Stops: P, R unchanged; Q renamed; S moved ~180 m; OLD only in v1; NEW only in v2.
* Calendars: WK weekdays, SAT Saturdays. v1 removes WK on 2026-12-08;
  v2 removes WK on 2026-12-25 and adds it on Sunday 2026-12-27.
* Common date 2026-11-06 (Friday); 2026-10-01 is only in v1; 2027-02-01 only in v2.
"""
from __future__ import annotations
import csv
import io
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

SOURCE = 'synthetic_valley'
OTHER_SOURCE = 'synthetic_other'
COMMON_DATE = '2026-11-06'
ONLY_V1_DATE = '2026-10-01'
ONLY_V2_DATE = '2027-02-01'


def _csv(rows):
    out = io.StringIO(newline='')
    writer = csv.DictWriter(out, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue().encode('utf-8')


def _zip(path: Path, feed: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(path, 'w', compression=ZIP_DEFLATED) as z:
        for name, rows in feed.items():
            if rows:
                z.writestr(name+'.txt', _csv(rows))
    return path


def _stop(stop_id, name, lat, lon):
    return dict(stop_id=stop_id, stop_name=name, stop_lat=str(lat), stop_lon=str(lon))


def _trip(route, service, trip, headsign, stops_times):
    trip_row = dict(route_id=route, service_id=service, trip_id=trip, trip_headsign=headsign, shape_id='')
    times = [dict(trip_id=trip, arrival_time=t, departure_time=t, stop_id=s, stop_sequence=str(i))
             for i, (s, t) in enumerate(stops_times, 1)]
    return trip_row, times


def _feed(version: int) -> dict:
    v1 = version == 1
    stops = [_stop('P', 'SYNTHETIC Piazza', 40.000, 9.000),
             _stop('Q', 'SYNTHETIC Quarry' if v1 else 'SYNTHETIC Quarry Gate', 40.010, 9.000),
             _stop('R', 'SYNTHETIC Ridge', 40.020, 9.000),
             _stop('S', 'SYNTHETIC Station', 40.030, 9.010) if v1 else _stop('S', 'SYNTHETIC Station', 40.0315, 9.0105),
             _stop('OLD', 'SYNTHETIC Old Mill', 40.040, 9.020) if v1 else _stop('NEW', 'SYNTHETIC New Park', 40.050, 9.030)]
    routes = [dict(route_id='R1', agency_id='SV', route_short_name='1', route_long_name='Piazza - Ridge', route_type='3'),
              dict(route_id='R2', agency_id='SV', route_short_name='2',
                   route_long_name='Piazza - Station' if v1 else 'Piazza - Station via Quarry', route_type='3'),
              dict(route_id='N1', agency_id='SV', route_short_name='N1', route_long_name='Night owl', route_type='3'),
              dict(route_id='FX', agency_id='SV', route_short_name='FX', route_long_name='Exact shuttle', route_type='3'),
              dict(route_id='FW', agency_id='SV', route_short_name='FW', route_long_name='Headway shuttle', route_type='3')]
    routes.append(dict(route_id='R9', agency_id='SV', route_short_name='9', route_long_name='Old Mill', route_type='3') if v1
                  else dict(route_id='R10', agency_id='SV', route_short_name='10', route_long_name='New Park', route_type='3'))
    plan = [('R1', 'WK', 'R1a', 'Ridge', [('P', '07:00:00'), ('Q', '07:05:00'), ('R', '07:10:00')]),
            ('R1', 'WK', 'R1b', 'Ridge', [('P', '17:00:00'), ('Q', '17:05:00'), ('R', '17:10:00')]),
            ('R1', 'SAT', 'R1s', 'Ridge', [('P', '09:00:00'), ('Q', '09:05:00'), ('R', '09:10:00')]),
            ('N1', 'WK', 'N1a', 'Night', [('P', '23:40:00'), ('R', '24:20:00')]),
            ('FX', 'WK', 'FXa', 'Shuttle', [('P', '08:00:00'), ('Q', '08:06:00')]),
            ('FW', 'WK', 'FWa', 'Shuttle', [('P', '10:00:00'), ('R', '10:09:00')])]
    if v1:
        plan += [('R2', 'WK', 'R2a', 'Station', [('P', '07:30:00'), ('S', '07:45:00')]),
                 ('R2', 'WK', 'R2b', 'Station', [('P', '12:30:00'), ('S', '12:45:00')]),
                 ('R9', 'WK', 'R9a', 'Old Mill', [('S', '08:00:00'), ('OLD', '08:10:00')])]
    else:
        plan += [('R2', 'WK', f'R2{x}', 'Station via Quarry', [('P', t), ('Q', t[:3]+'38:00'), ('S', t[:3]+'50:00')])
                 for x, t in (('a', '07:30:00'), ('b', '12:30:00'), ('c', '18:30:00'))]
        plan += [('R10', 'WK', 'R10a', 'New Park', [('S', '08:00:00'), ('NEW', '08:12:00')])]
    trips, stop_times = [], []
    for route, service, trip, headsign, st in plan:
        t, times = _trip(route, service, trip, headsign, st)
        trips.append(t)
        stop_times += times
    start, end = ('20260901', '20261231') if v1 else ('20261101', '20270331')
    calendar = [dict(service_id='WK', monday='1', tuesday='1', wednesday='1', thursday='1', friday='1', saturday='0',
                     sunday='0', start_date=start, end_date=end),
                dict(service_id='SAT', monday='0', tuesday='0', wednesday='0', thursday='0', friday='0', saturday='1',
                     sunday='0', start_date=start, end_date=end)]
    calendar_dates = ([dict(service_id='WK', date='20261208', exception_type='2')] if v1 else
                      [dict(service_id='WK', date='20261225', exception_type='2'),
                       dict(service_id='WK', date='20261227', exception_type='1')])
    return {
        'agency': [dict(agency_id='SV', agency_name='SYNTHETIC Valley Transit', agency_url='https://example.invalid',
                        agency_timezone='Europe/Rome')],
        'feed_info': [dict(feed_publisher_name='SYNTHETIC Valley Transit', feed_publisher_url='https://example.invalid',
                           feed_lang='it', feed_start_date=start, feed_end_date=end,
                           feed_version='2026.09' if v1 else '2026.11')],
        'routes': routes, 'stops': stops, 'trips': trips, 'stop_times': stop_times,
        'calendar': calendar, 'calendar_dates': calendar_dates,
        'frequencies': [dict(trip_id='FXa', start_time='08:00:00', end_time='09:00:00', headway_secs='1200', exact_times='1'),
                        dict(trip_id='FWa', start_time='10:00:00', end_time='11:00:00', headway_secs='900', exact_times='0')],
    }


def _other() -> dict:
    """A different operator reusing IDs R1 and P with unrelated content."""
    return {
        'agency': [dict(agency_id='OT', agency_name='SYNTHETIC Other Lines', agency_url='https://example.invalid',
                        agency_timezone='Europe/Rome')],
        'routes': [dict(route_id='R1', agency_id='OT', route_short_name='X1', route_long_name='Unrelated line', route_type='3')],
        'stops': [_stop('P', 'SYNTHETIC Elsewhere', 41.0, 9.5), _stop('Z', 'SYNTHETIC Zenith', 41.01, 9.5)],
        'trips': [dict(route_id='R1', service_id='ALL', trip_id='X1a', trip_headsign='Zenith', shape_id='')],
        'stop_times': [dict(trip_id='X1a', arrival_time='06:00:00', departure_time='06:00:00', stop_id='P', stop_sequence='1'),
                       dict(trip_id='X1a', arrival_time='06:20:00', departure_time='06:20:00', stop_id='Z', stop_sequence='2')],
        'calendar': [dict(service_id='ALL', monday='1', tuesday='1', wednesday='1', thursday='1', friday='1', saturday='1',
                          sunday='1', start_date='20260101', end_date='20271231')],
    }


def build(folder: Path) -> dict[str, Path]:
    folder = Path(folder)
    return {'v1': _zip(folder/'synthetic_valley_v1.zip', _feed(1)),
            'v2': _zip(folder/'synthetic_valley_v2.zip', _feed(2)),
            'other': _zip(folder/'synthetic_other.zip', _other())}


def import_all(con, folder: Path, archive_dir: Path) -> dict[str, str]:
    """Import v1, then v2, then the other operator. Returns snapshot IDs."""
    from transport_atlas.ingest import import_file
    paths = build(folder)
    ids = {}
    for key, source in (('v1', SOURCE), ('v2', SOURCE), ('other', OTHER_SOURCE)):
        ids[key] = import_file(con, paths[key], source_id=source, archive_dir=archive_dir,
                               publisher='SYNTHETIC fixture (not a real operator)')['snapshot_id']
    return ids
