"""Phase 8A: per-trip timetable differences (read-only), on invented feeds in disposable databases."""
from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from transport_atlas import timetable_diff as ttd
from transport_atlas.api import create_app
from transport_atlas.history import HistoryError, SnapshotHistory
from transport_atlas.ingest import import_file
from transport_atlas.storage import connect
from . import history_fixtures as hf
from . import timetable_fixtures as tf

DAY = date.fromisoformat(tf.DAY)


@pytest.fixture(params=['sqlite', 'duckdb'])
def db(request, tmp_path):
    if request.param == 'duckdb':
        pytest.importorskip('duckdb')
    path = tmp_path / ('t.sqlite' if request.param == 'sqlite' else 't.duckdb')
    zips = tf.build(tmp_path / 'zips')
    other = hf.build(tmp_path / 'other')['other']
    con = connect(path, request.param)
    try:
        b = import_file(con, zips['B'], source_id=tf.SOURCE, archive_dir=tmp_path / 'raw', publisher='SYNTHETIC')
        a = import_file(con, zips['A'], source_id=tf.SOURCE, archive_dir=tmp_path / 'raw', activate=False)
        o = import_file(con, other, source_id=hf.OTHER_SOURCE, archive_dir=tmp_path / 'raw', publisher='SYNTHETIC')
    finally:
        con.close()
    return {'path': path, 'backend': request.param, 'A': a['snapshot_id'], 'B': b['snapshot_id'], 'other': o['snapshot_id']}


def diff(db, **kw):
    con = connect(db['path'], db['backend'])
    try:
        return SnapshotHistory(con).timetable_diff(tf.SOURCE, kw.pop('baseline', db['A']), kw.pop('comparison', db['B']),
                                                   **{'service_date': DAY, 'status': 'all', 'limit': 500, **kw})
    finally:
        con.close()


def by_trip(result):
    out = {}
    for r in result['records']:
        out[(r['baseline'] or {}).get('trip_id'), (r['comparison'] or {}).get('trip_id')] = r
    return out


def test_every_case_is_classified_and_reconciled(db):
    r = diff(db)
    assert r['status'] == 'ok' and r['policy'] == 'same_date' and r['synthetic'] is True
    assert r['counts'] == {'changed': 4, 'added': 2, 'removed': 2, 'ambiguous': 3, 'unsupported': 1, 'unchanged': 2}
    assert r['reconciliation'] == {'baseline_trips': 10, 'baseline_accounted': 10, 'comparison_trips': 11,
                                   'comparison_accounted': 11, 'complete': True}
    assert r['matched_by'] == {'same_trip_id': 5, 'identical_timetable_new_id': 1, 'unique_similar_trip': 1}
    t = by_trip(r)
    same = t[('T_SAME', 'T_SAME')]
    assert same['status'] == 'unchanged' and same['shape_id_changed'] is True and same['change_kinds'] == []
    assert r['shape_id_only_changes'] == 1
    times = t[('T_TIMES', 'T_TIMES')]
    assert times['status'] == 'changed' and times['change_kinds'] == ['stop_times']
    renum = t[('OLDREN', 'NEWREN')]
    assert renum['status'] == 'unchanged' and renum['match']['stage'] == 'identical_timetable_new_id'
    shift = t[('SHIFT_A', 'SHIFT_B')]
    assert shift['status'] == 'changed' and shift['match']['stage'] == 'unique_similar_trip'
    assert shift['match']['departure_shift_seconds'] == 300 and shift['match']['stop_sequence_similarity'] == 1.0
    assert 'departure_time' in shift['change_kinds'] and '09:00:00 → 09:05:00 (+5 min)' in shift['summary']
    add = t[('ADDSTOP', 'ADDSTOP')]
    assert {'stop_count', 'stop_pattern'} <= set(add['change_kinds']) and '3 → 4 stops' in add['summary']
    night = t[('NIGHT', 'NIGHT')]
    assert night['baseline']['last_arrival'] == '25:10:00' and night['comparison']['last_arrival'] == '25:15:00'
    assert night['change_kinds'] == ['last_arrival', 'stop_times']
    assert t[('FREQ', 'FREQ')]['status'] == 'unsupported'
    amb = [x for x in r['records'] if x['status'] == 'ambiguous']
    assert sorted((x['baseline'] or x['comparison'])['trip_id'] for x in amb) == ['AMB_A', 'AMB_B1', 'AMB_B2']
    amb_a = next(x for x in amb if x['baseline'])
    assert sorted(c['comparison_trip_id'] for c in amb_a['candidates']) == ['AMB_B1', 'AMB_B2']
    assert t[('GONE', None)]['status'] == 'removed' and t[('EXC', None)]['status'] == 'removed'  # calendar_dates removal
    assert t[(None, 'NEWTRIP')]['status'] == 'added' and t[(None, 'EXTRA')]['status'] == 'added'  # calendar_dates addition
    assert 'not proof' in r['note'] and set(r['matching']) == {'same_trip_id', 'identical_timetable_new_id',
                                                                 'unique_similar_trip'}


def test_stop_by_stop_differences():
    a = {'trip_id': 'a', 'route_id': 'R', 'direction_id': 0, 'headsign': 'x', 'service_id': 's', 'shape_id': None,
         'frequencies': [], 'stops': [(1, 'S1', 600, 600), (2, 'S2', 900, 960), (3, 'S3', 1200, 1200)]}
    b = {**a, 'trip_id': 'b', 'stops': [(1, 'S1', 600, 600), (2, 'S2', 960, 1020), (3, 'S9', 1100, 1100),
                                         (4, 'S3', 1300, 1300)]}
    out = ttd.stop_differences(a, b)
    assert out[0] == {'change': 'time', 'stop_id': 'S2', 'baseline_sequence': 2, 'comparison_sequence': 2,
                      'baseline_arrival': '00:15:00', 'comparison_arrival': '00:16:00',
                      'baseline_departure': '00:16:00', 'comparison_departure': '00:17:00', 'shift_seconds': 60}
    assert {'change': 'added_stop', 'stop_id': 'S9', 'comparison_sequence': 3, 'comparison_arrival': '00:18:20',
            'comparison_departure': '00:18:20'} in out
    assert any(d['change'] == 'time' and d['stop_id'] == 'S3' for d in out)
    assert ttd.gtfs_time(25 * 3600 + 600) == '25:10:00' and ttd.gtfs_time(None) is None


def _t(trip_id, route, start, stops, direction=0):
    return {'trip_id': trip_id, 'route_id': route, 'direction_id': direction, 'headsign': None, 'service_id': 's',
            'shape_id': None, 'frequencies': [], 'stops': [(i + 1, s, start + 300 * i, start + 300 * i)
                                                           for i, s in enumerate(stops)]}


def test_a_claimed_candidate_is_not_called_ambiguous_and_matches_are_never_forced():
    old = {'A1': _t('A1', 'R9', 36000, ['S1', 'S2', 'S3']), 'A2': _t('A2', 'R9', 36600, ['S1', 'S2', 'S3'])}
    new = {'B1': _t('B1', 'R9', 36060, ['S1', 'S2', 'S3'])}
    r = ttd.diff_trips(old, new)
    t = by_trip(r)
    assert t[('A1', 'B1')]['status'] == 'changed' and t[('A1', 'B1')]['match']['stage'] == 'unique_similar_trip'
    assert t[('A2', None)]['status'] == 'removed' and 'matched another trip more closely' in t[('A2', None)]['note']
    # Different direction or too far apart: never matched.
    old = {'A': _t('A', 'R9', 36000, ['S1', 'S2', 'S3'])}
    assert ttd.diff_trips(old, {'B': _t('B', 'R9', 36000, ['S1', 'S2', 'S3'], direction=1)})['counts']['added'] == 1
    assert ttd.diff_trips(old, {'B': _t('B', 'R9', 36000 + 3601, ['S1', 'S2', 'S3'])})['counts']['removed'] == 1
    # Too different a stop sequence: never matched.
    assert ttd.diff_trips(old, {'B': _t('B', 'R9', 36000, ['S7', 'S8', 'S9'])})['counts']['added'] == 1
    # Two identical timetables on one side: no unique renumbering, so ambiguous rather than forced.
    twins = {'B1': _t('B1', 'R9', 36000, ['S1', 'S2', 'S3']), 'B2': _t('B2', 'R9', 36000, ['S1', 'S2', 'S3'])}
    assert ttd.diff_trips(old, twins)['counts']['ambiguous'] == 3


def test_filters_pagination_and_stable_order(db):
    r = diff(db, status='differences', limit=3)
    assert r['page']['matching'] == 12 and len(r['records']) == 3 and r['page']['next_offset'] == 3
    rest = diff(db, status='differences', limit=100, offset=3)
    assert len(rest['records']) == 9 and rest['page']['truncated'] is False
    ids = [x['id'] for x in r['records'] + rest['records']]
    assert ids == [x['id'] for x in diff(db, status='differences')['records']]
    order = [(x['route_id'], x['departure'] or '99') for x in diff(db)['records']]
    assert order == sorted(order)
    assert {x['status'] for x in diff(db, status='changed')['records']} == {'changed'}
    assert {x['route_id'] for x in diff(db, route='R1')['records']} == {'R1'}
    assert diff(db, status='unchanged')['page']['matching'] == 2


def test_incomparable_and_unsupported_cases(db):
    assert diff(db, service_date=date.fromisoformat(tf.OUTSIDE))['status'] == 'incomparable'
    r = diff(db, policy='different_dates', service_date=None, baseline_date=DAY, comparison_date=DAY)
    assert r['status'] == 'incomparable' and 'same service date' in r['reason']
    sat = diff(db, service_date=date.fromisoformat(tf.SATURDAY))
    assert sat['status'] == 'ok' and sat['counts'] == dict.fromkeys(ttd.STATUSES, 0)
    assert any(n['code'] == 'no_service' for n in sat['notices'])
    same = diff(db, comparison=db['A'])
    assert set(same['counts']) and same['counts']['changed'] == 0 and same['counts']['added'] == 0
    assert any(n['code'] == 'same_snapshot' for n in same['notices'])
    with pytest.raises(HistoryError, match='different operators'):
        diff(db, comparison=db['other'])
    with pytest.raises(HistoryError, match='status must be'):
        diff(db, status='bogus')


def test_api_is_additive_and_read_only(db):
    with TestClient(create_app(db['path'], db['backend'])) as client:
        before = client.get('/api/v1/snapshots').json()
        cmp = client.get(f'/api/v1/history/{tf.SOURCE}/compare', params={
            'baseline': db['A'], 'comparison': db['B'], 'policy': 'same_date', 'date': tf.DAY})
        assert cmp.status_code == 200 and 'records' not in cmp.json()  # existing output unchanged
        r = client.get(f'/api/v1/history/{tf.SOURCE}/timetable-diff', params={
            'baseline': db['A'], 'comparison': db['B'], 'date': tf.DAY, 'status': 'changed', 'limit': 2})
        body = r.json()
        assert r.status_code == 200 and body['status'] == 'ok' and len(body['records']) == 2
        assert body['baseline']['snapshot_id'] == db['A'] and body['page']['next_offset'] == 2
        assert client.get(f'/api/v1/history/{tf.SOURCE}/timetable-diff', params={
            'baseline': db['A'], 'comparison': db['B'], 'date': tf.DAY, 'limit': 501}).status_code == 422
        assert client.get(f'/api/v1/history/{tf.SOURCE}/timetable-diff', params={
            'baseline': db['A'], 'comparison': db['other'], 'date': tf.DAY}).status_code == 422
        assert client.get('/api/v1/snapshots').json() == before


def test_trip_cap_reports_unsupported(db, monkeypatch):
    monkeypatch.setattr(ttd, 'MAX_TRIPS_PER_SIDE', 3)
    r = diff(db)
    assert r['status'] == 'unsupported' and 'cap' in r['reason']


def test_trip_detail_endpoint(db):
    with TestClient(create_app(db['path'], db['backend'])) as client:
        r = client.get(f'/api/v1/history/{tf.SOURCE}/timetable-trip', params={
            'baseline': db['A'], 'comparison': db['B'], 'baseline_trip': 'ADDSTOP', 'comparison_trip': 'ADDSTOP'}).json()
        assert [s['stop_id'] for s in r['comparison_stops']] == ['S1', 'S2', 'S7', 'S3']
        assert any(d['change'] == 'added_stop' and d['stop_id'] == 'S7' and d['name'] == 'SYNTHETIC Stop 7'
                   for d in r['differences'])
        night = client.get(f'/api/v1/history/{tf.SOURCE}/timetable-trip', params={
            'baseline': db['A'], 'comparison': db['B'], 'baseline_trip': 'NIGHT', 'comparison_trip': 'NIGHT'}).json()
        assert night['baseline_stops'][-1]['arrival'] == '25:10:00'
        gone = client.get(f'/api/v1/history/{tf.SOURCE}/timetable-trip', params={
            'baseline': db['A'], 'comparison': db['B'], 'baseline_trip': 'GONE'}).json()
        assert gone['comparison_stops'] is None and gone['differences'] is None and len(gone['baseline_stops']) == 3
        assert client.get(f'/api/v1/history/{tf.SOURCE}/timetable-trip', params={
            'baseline': db['A'], 'comparison': db['B'], 'baseline_trip': 'NOPE'}).status_code == 404
        assert client.get(f'/api/v1/history/{tf.SOURCE}/timetable-trip', params={
            'baseline': db['A'], 'comparison': db['other'], 'baseline_trip': 'GONE'}).status_code == 422
