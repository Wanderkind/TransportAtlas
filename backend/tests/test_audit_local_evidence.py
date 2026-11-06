"""Exercise independent audit arithmetic without opening the local feed database."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import duckdb

spec = spec_from_file_location('audit_local_evidence', Path(__file__).resolve().parents[2] / 'scripts/audit_local_evidence.py')
audit = module_from_spec(spec)
spec.loader.exec_module(audit)


def test_independent_calendar_exceptions_and_frequency_precision():
    weekly = dict(service_id='weekly', start_date='20261101', end_date='20261108',
                  **{day: '1' for day in audit.WEEK})
    exceptions = [dict(service_id='weekly', date='20261106', exception_type='2'),
                  dict(service_id='added', date='20261106', exception_type='1')]
    trips = [dict(trip_id='removed', service_id='weekly', route_id='r0'),
             dict(trip_id='plain', service_id='added', route_id='r1'),
             dict(trip_id='exact', service_id='added', route_id='r1'),
             dict(trip_id='window', service_id='added', route_id='r2')]
    frequencies = {
        'exact': [dict(start_time='25:00:00', end_time='25:20:01', headway_secs='600', exact_times='1')],
        'window': [dict(start_time='08:00:00', end_time='09:00:00', headway_secs='600', exact_times='0')],
    }
    raw = audit.raw_day([weekly], exceptions, trips, frequencies, '2026-11-06')
    assert raw == dict(active_services=1, active_trip_templates=3, active_route_records=2,
                       scheduled_trip_instances=4, non_exact_frequency_windows=1,
                       exception_additions=1, exception_removals=1)
    con = duckdb.connect(':memory:')
    try:
        con.execute('CREATE TABLE calendars(snapshot_id VARCHAR, service_id VARCHAR, start_date VARCHAR, end_date VARCHAR, friday INTEGER, monday INTEGER)')
        con.execute("INSERT INTO calendars VALUES ('s','weekly','2026-11-01','2026-11-08',1,1)")
        con.execute('CREATE TABLE calendar_exceptions(snapshot_id VARCHAR, service_id VARCHAR, service_date VARCHAR, exception_type INTEGER)')
        con.executemany('INSERT INTO calendar_exceptions VALUES (?,?,?,?)', [('s',r['service_id'],'2026-11-06',int(r['exception_type'])) for r in exceptions])
        con.execute('CREATE TABLE trips(snapshot_id VARCHAR, trip_id VARCHAR, service_id VARCHAR, route_id VARCHAR)')
        con.executemany('INSERT INTO trips VALUES (?,?,?,?)', [('s',t['trip_id'],t['service_id'],t['route_id']) for t in trips])
        con.execute('CREATE TABLE frequencies(snapshot_id VARCHAR, trip_id VARCHAR, start_sec INTEGER, end_sec INTEGER, headway_secs INTEGER, exact_times INTEGER)')
        con.executemany('INSERT INTO frequencies VALUES (?,?,?,?,?,?)', [('s','exact',90000,91201,600,1),('s','window',28800,32400,600,0)])
        sql = audit.sql_day(con,'s','2026-11-06')
        assert sql == {k:raw[k] for k in sql}
        # Weekly service must expire after its inclusive end; a removed service
        # does not remain removed on another day.
        assert audit.raw_day([weekly],exceptions,trips,frequencies,'2026-11-07')['scheduled_trip_instances'] == 1
        assert audit.raw_day([weekly],exceptions,trips,frequencies,'2026-11-09')['scheduled_trip_instances'] == 0
        assert audit.sql_day(con,'s','2026-11-09')['scheduled_trip_instances'] == 0
    finally:
        con.close()
