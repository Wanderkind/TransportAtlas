"""Synthetic regressions for service-day and exact-instance correctness."""
from datetime import date, timezone
from pathlib import Path

import pytest

from transport_atlas.ingest import import_file
from transport_atlas.route_details import route_variants
from transport_atlas.schedule import active_services, daily_summary, gtfs_instant, stop_board, trip_stop_times
from transport_atlas.storage import connect
from transport_atlas.timetable import route_timetable


@pytest.fixture(params=['sqlite', 'duckdb'])
def timetable_db(tmp_path, request):
    con = connect(tmp_path / 'audit.db', request.param)
    fixture = Path(__file__).parent / 'fixtures' / 'synthetic_sardinia.zip'
    sid = import_file(con, fixture, source_id='synthetic-audit', archive_dir=tmp_path / 'raw')['snapshot_id']
    yield con, sid
    con.close()


def _trips(con, sid, definitions, service_day):
    """Replace imported templates with small, explicit timetable scenarios."""
    for table in ('frequencies', 'stop_times', 'trips', 'calendars', 'calendar_exceptions'):
        con.execute(f'DELETE FROM {table} WHERE snapshot_id=?', (sid,))
    con.execute('INSERT INTO calendar_exceptions VALUES(?,?,?,?)', (sid, 'AUDIT', service_day, 1))
    for tid, direction, stops in definitions:
        con.execute('INSERT INTO trips VALUES(?,?,?,?,?,?,?,?)',
                    (sid, tid, 'E', 'AUDIT', None, direction, 'Synthetic destination', None))
        for sequence, (stop_id, seconds) in enumerate(stops, 1):
            con.execute('INSERT INTO stop_times VALUES(?,?,?,?,?,?,?,?,?)',
                        (sid, tid, sequence, stop_id, seconds, seconds, 0, 0, 1))


def test_stop_board_orders_fall_back_departures_by_instant(timetable_db):
    con, sid = timetable_db
    _trips(con, sid, [('before-fold', 0, [('A', 6600), ('B', 6900)]),
                     ('after-fold', 0, [('A', 7800), ('B', 8100)])], '2026-10-25')
    board = stop_board(con, sid, 'A', '2026-10-25')
    assert [d['trip_id'] for d in board['departures']] == ['before-fold', 'after-fold']
    assert [d['local_departure'] for d in board['departures']] == [
        '2026-10-25T02:50:00+02:00', '2026-10-25T02:10:00+01:00']


def test_timetable_fall_back_first_last_and_intervals_follow_instants(timetable_db):
    con, sid = timetable_db
    _trips(con, sid, [('before-fold', 0, [('A', 6600), ('B', 6900)]),
                     ('after-fold', 0, [('A', 7800), ('B', 8100)])], '2026-10-25')
    timetable = route_timetable(con, sid, 'E', date(2026, 10, 25), limit=1)
    assert timetable['first_departure'] == '2026-10-25T02:50:00+02:00'
    assert timetable['last_departure'] == '2026-10-25T02:10:00+01:00'
    assert timetable['departures'][0]['trip_id'] == 'before-fold'
    assert timetable['variants'][0]['median_interval_minutes'] == 20
    assert timetable['total_departures'] == 2
    assert timetable['truncated']


@pytest.mark.parametrize('surface', ['timetable', 'board'])
def test_frequency_window_order_follows_instants_across_fall_back(timetable_db, surface):
    con, sid = timetable_db
    _trips(con, sid, [('after-fold', 0, [('A', 0), ('B', 300)]),
                     ('before-fold', 0, [('A', 0), ('B', 300)])], '2026-10-25')
    for tid, start in [('after-fold', 7800), ('before-fold', 6600)]:
        con.execute('INSERT INTO frequencies VALUES(?,?,?,?,?,?)', (sid, tid, start, start + 300, 300, 0))
    if surface == 'timetable':
        timetable = route_timetable(con, sid, 'E', date(2026, 10, 25), limit=1)
        assert timetable['departures'] == []
        assert timetable['frequency_windows'][0]['trip_id'] == 'before-fold'
        assert timetable['windows_truncated']
    else:
        board = stop_board(con, sid, 'A', '2026-10-25')
        assert board['departures'] == []
        assert [w['trip_id'] for w in board['frequency_windows']] == ['before-fold', 'after-fold']


@pytest.mark.parametrize('exact_times', [0, 1])
def test_board_lookback_includes_frequency_start_plus_downstream_offset(timetable_db, exact_times):
    con, sid = timetable_db
    _trips(con, sid, [('long-template', 0, [('A', 0), ('B', 71 * 3600)])], '2026-11-06')
    con.execute('INSERT INTO frequencies VALUES(?,?,?,?,?,?)',
                (sid, 'long-template', 71 * 3600, 72 * 3600, 3600, exact_times))
    board = stop_board(con, sid, 'B', '2026-11-11')
    if exact_times:
        assert len(board['departures']) == 1
        assert board['departures'][0]['service_date'] == '2026-11-06'
        assert board['departures'][0]['gtfs_time'] == '142:00:00'
        assert board['departures'][0]['local_departure'] == '2026-11-11T22:00:00+01:00'
        assert board['frequency_windows'] == []
    else:
        assert board['departures'] == []
        assert len(board['frequency_windows']) == 1
        assert board['frequency_windows'][0]['service_date'] == '2026-11-06'
        assert board['frequency_windows'][0]['start_local'] == '2026-11-11T22:00:00+01:00'


def test_exact_instance_stop_offsets_and_full_counts_survive_caps(timetable_db):
    con, sid = timetable_db
    con.execute("UPDATE trips SET route_id='E' WHERE snapshot_id=? AND trip_id='T_FREQ'", (sid,))
    con.execute('INSERT INTO frequencies VALUES(?,?,?,?,?,?)',
                (sid, 'T_FREQ', 11 * 3600, 12 * 3600, 900, 0))
    timetable = route_timetable(con, sid, 'E', date(2026, 11, 6), limit=1)
    assert timetable['total_departures'] == 3
    assert len(timetable['departures']) == 1 and timetable['truncated']
    assert len(timetable['frequency_windows']) == 1 and timetable['windows_truncated']
    assert timetable['first_departure'] == '2026-11-06T08:00:00+01:00'
    assert timetable['last_departure'] == '2026-11-06T08:40:00+01:00'
    assert sum(v['scheduled_departures'] for v in timetable['variants']) == 3
    assert sum(v['frequency_windows'] for v in timetable['variants']) == 2
    complete = route_timetable(con, sid, 'E', date(2026, 11, 6))
    instance = complete['departures'][-1]
    assert instance['departure_seconds'] == 8 * 3600 + 40 * 60
    assert instance['template_first_seconds'] == 9 * 3600
    # This is the offset contract consumed by the selected-instance inspector.
    offset = instance['departure_seconds'] - instance['template_first_seconds']
    detail = trip_stop_times(con, sid, instance['trip_id'])
    assert [s['departure_sec'] + offset for s in detail['stops']] == [31200, 32100]
    assert instance['arrival_local'] == '2026-11-06T08:55:00+01:00'
    board = stop_board(con, sid, 'B', '2026-11-06')
    exact = [d for d in board['departures'] if d['trip_id'] == 'T_EXACT']
    assert [d['gtfs_time'] for d in exact] == ['08:15:00', '08:35:00', '08:55:00']
    assert not any(d['trip_id'] == 'T_FREQ' for d in board['departures'])
    assert len([w for w in board['frequency_windows'] if w['trip_id'] == 'T_FREQ']) == 2
    summary = daily_summary(con, '2026-11-06')['snapshots'][0]
    assert summary['scheduled_trip_instances'] == 4  # Three exact instances and the overnight template.
    assert summary['non_exact_frequency_windows'] == 2


def test_service_bounds_and_exception_override(timetable_db):
    con, sid = timetable_db
    con.execute('UPDATE calendars SET monday=1,tuesday=1,wednesday=1,thursday=1,friday=1,saturday=1,sunday=1 WHERE snapshot_id=?', (sid,))
    assert active_services(con, sid, '2026-10-31') == set()
    assert active_services(con, sid, '2026-11-01') == {'WEEK'}
    assert active_services(con, sid, '2026-11-30') == {'WEEK'}
    assert active_services(con, sid, '2026-11-13') == set()
    assert active_services(con, sid, '2026-12-01') == set()
    con.execute('INSERT INTO calendar_exceptions VALUES(?,?,?,?)', (sid, 'WEEK', '2026-12-01', 1))
    assert active_services(con, sid, '2026-12-01') == {'WEEK'}
    # The previous day's removed service cannot reappear on an overnight board.
    assert not any(d['trip_id'] == 'T_NIGHT' for d in stop_board(con, sid, 'B', '2026-11-14')['departures'])


@pytest.mark.parametrize('day,midnight', [
    ('2026-03-29', '2026-03-28T23:00:00+01:00'),
    ('2026-10-25', '2026-10-25T01:00:00+02:00'),
])
def test_dst_origin_is_twelve_elapsed_hours_before_local_noon(day, midnight):
    origin = gtfs_instant(day, 0, 'Europe/Rome')
    noon = gtfs_instant(day, 12 * 3600, 'Europe/Rome')
    assert origin.isoformat() == midnight
    assert noon.isoformat()[:16] == day + 'T12:00'
    assert (noon.astimezone(timezone.utc) - origin.astimezone(timezone.utc)).total_seconds() == 12 * 3600


def test_stop_display_timezone_preserves_agency_service_day(timetable_db):
    con, sid = timetable_db
    con.execute("UPDATE stops SET timezone='Asia/Seoul' WHERE snapshot_id=?", (sid,))
    board = stop_board(con, sid, 'B', '2026-11-07')
    night = [d for d in board['departures'] if d['trip_id'] == 'T_NIGHT']
    assert len(night) == 1
    assert night[0]['service_date'] == '2026-11-06'
    assert night[0]['gtfs_time'] == '25:15:00'
    assert night[0]['local_departure'] == '2026-11-07T09:15:00+09:00'


def test_variants_preserve_stop_order_direction_and_boarding_rules(timetable_db):
    con, sid = timetable_db
    _trips(con, sid, [
        ('base', 0, [('A', 3600), ('B', 7200)]),
        ('same', 0, [('A', 10800), ('B', 14400)]),
        ('direction', 1, [('A', 3600), ('B', 7200)]),
        ('reverse', 0, [('B', 3600), ('A', 7200)]),
        ('no-pickup', 0, [('A', 3600), ('B', 7200)]),
    ], '2026-11-06')
    con.execute("UPDATE stop_times SET pickup_type=1 WHERE snapshot_id=? AND trip_id='no-pickup' AND stop_sequence=1", (sid,))
    # GTFS sequence labels need not be contiguous or match other templates.
    con.execute("UPDATE stop_times SET stop_sequence=stop_sequence*10 WHERE snapshot_id=? AND trip_id='same'", (sid,))
    variants, mapping = route_variants(con, sid, 'E')
    assert len(variants) == 4
    assert mapping['base'] == mapping['same']
    assert len({mapping[t] for t in ['base', 'direction', 'reverse', 'no-pickup']}) == 4
    variant = next(v for v in variants if v['variant_id'] == mapping['reverse'])
    assert [s['stop_id'] for s in variant['stops']] == ['B', 'A']
