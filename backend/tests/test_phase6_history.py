"""Phase 6: read-only snapshot inventory and historical comparison (synthetic feeds only)."""
from datetime import date
import hashlib
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from transport_atlas.api import create_app
from transport_atlas.history import SnapshotHistory, HistoryError, NotFound
from transport_atlas.ingest import import_file
from transport_atlas.storage import connect, rows
from . import history_fixtures as hf

ROOT = Path(__file__).resolve().parents[2]
D = date.fromisoformat


@pytest.fixture
def db(tmp_path):
    con = connect(tmp_path/'history.sqlite', 'sqlite')
    ids = hf.import_all(con, tmp_path/'zips', tmp_path/'raw')
    yield con, ids, tmp_path
    con.close()


def selection(con):
    return sorted((r['source_id'], r['snapshot_id']) for r in rows(con, 'SELECT * FROM selection_members'))


def compare(con, ids, **kw):
    kw.setdefault('policy', 'same_date')
    if kw['policy'] == 'same_date':
        kw.setdefault('service_date', D(hf.COMMON_DATE))
    return SnapshotHistory(con).compare(hf.SOURCE, kw.pop('a', ids['v1']), kw.pop('b', ids['v2']), **kw)


def test_both_versions_survive_import_and_inventory_reports_them(db):
    con, ids, tmp = db
    assert ids['v1'] != ids['v2']
    # Importing v2 moves the *import* selection pointer (existing behaviour) but keeps v1 intact.
    assert selection(con) == [(hf.OTHER_SOURCE, ids['other']), (hf.SOURCE, ids['v2'])]
    # Re-importing identical bytes is a duplicate: no new snapshot, selection untouched.
    again = import_file(con, tmp/'zips'/'synthetic_valley_v1.zip', source_id=hf.SOURCE, archive_dir=tmp/'raw')
    assert again == {'status': 'unchanged', 'snapshot_id': ids['v1'], 'source_id': hf.SOURCE, 'sha256': again['sha256']}
    assert selection(con) == [(hf.OTHER_SOURCE, ids['other']), (hf.SOURCE, ids['v2'])]
    assert len(rows(con, 'SELECT snapshot_id FROM feed_snapshots WHERE source_id=?', (hf.SOURCE,))) == 2
    history = SnapshotHistory(con)
    inventory = history.inventory(hf.SOURCE, D(hf.ONLY_V1_DATE))
    by_id = {s['snapshot_id']: s for s in inventory['snapshots']}
    v1, v2 = by_id[ids['v1']], by_id[ids['v2']]
    assert v1['counts'] == {'route_records': 6, 'routes_with_trip_references': 6, 'stop_records': 5,
                            'stops_referenced_by_trips': 5, 'trip_templates': 9, 'shape_ids': 0}
    assert v2['counts']['trip_templates'] == 10
    assert v1['feed_info']['feed_version'] == '2026.09' and v2['feed_info']['feed_version'] == '2026.11'
    assert v1['feed_info']['declared_start'] == '2026-09-01' and v1['observed_coverage'] == {'start': '2026-09-01', 'end': '2026-12-31'}
    assert v1['date_in_coverage'] is True and v2['date_in_coverage'] is False
    assert v1['archive']['present'] and v1['archive']['sha256_verified'] is None
    assert 'not a publication' in v1['retrieval_note'] and v1['validation'] is None
    assert v2['selected'] and not v1['selected'] and inventory['comparison_possible']


def test_inventory_never_changes_selection_and_handles_one_snapshot_sources(db):
    con, ids, _ = db
    before = selection(con)
    history = SnapshotHistory(con)
    other = history.inventory(hf.OTHER_SOURCE)
    assert other['ready_snapshots'] == 1 and not other['comparison_possible'] and 'no version comparison' in other['note']
    history.inventory(hf.SOURCE, D(hf.COMMON_DATE), verify_archives=True)
    compare(con, ids)
    compare(con, ids, policy='different_dates', baseline_date=D(hf.ONLY_V1_DATE), comparison_date=D(hf.ONLY_V2_DATE))
    assert selection(con) == before
    sources = {s['source_id']: s for s in history.sources()}
    assert sources[hf.SOURCE]['comparison_possible'] and not sources[hf.OTHER_SOURCE]['comparison_possible']


def test_verify_archives_hashes_the_archived_zip(db):
    con, ids, _ = db
    item = next(s for s in SnapshotHistory(con).inventory(hf.SOURCE, verify_archives=True)['snapshots'] if s['snapshot_id'] == ids['v1'])
    assert item['archive']['sha256_verified'] is True


def test_failed_and_missing_archive_snapshots_are_listed_but_not_comparable(db):
    con, ids, tmp = db
    con.execute('INSERT INTO feed_snapshots VALUES(?,?,?,?,?,?,?,?,?)',
                ('failedsnap', hf.SOURCE, 'f'*64, '2026-11-01T00:00:00+00:00', str(tmp/'missing.zip'), 'failed', None, None, None))
    con.commit()
    item = next(s for s in SnapshotHistory(con).inventory(hf.SOURCE)['snapshots'] if s['snapshot_id'] == 'failedsnap')
    assert item['archive']['present'] is False and item['comparable'] is False and 'counts' not in item
    with pytest.raises(HistoryError, match='not ready'):
        compare(con, ids, b='failedsnap')


def test_same_date_comparison_counts_records_and_service(db):
    con, ids, _ = db
    r = compare(con, ids)
    s = r['summary']
    assert (s['trip_templates_on_date']['baseline'], s['trip_templates_on_date']['comparison']) == (8, 9)
    # Exact instances: one per plain template + 3 exact_times=1 departures; the window is separate.
    assert (s['exact_trip_instances']['baseline'], s['exact_trip_instances']['comparison']) == (9, 10)
    assert s['frequency_only_templates']['baseline'] == 1 and s['non_exact_windows']['comparison'] == 1
    assert s['trip_templates_on_date']['percent_change'] == 12.5
    assert s['trip_templates_on_date']['attributable_to_feed_version'] is True
    assert s['shape_ids']['percent_change'] is None and 'zero' in s['shape_ids']['percent_note']
    routes = {x['id']: x for x in r['routes']['records']}
    assert r['routes']['counts'] == {'added': 1, 'removed': 1, 'modified': 1, 'unchanged': 4}
    assert routes['R10']['status'] == 'added' and routes['R9']['status'] == 'removed'
    assert [c['field'] for c in routes['R2']['changes']] == ['long_name', 'headsigns']
    assert routes['R2']['baseline']['long_name'] == 'Piazza - Station'  # original values are inspectable
    assert (routes['R2']['service']['baseline'], routes['R2']['service']['comparison']) == (2, 3)
    stops = {x['id']: x for x in r['stops']['records']}
    assert r['stops']['counts'] == {'added': 1, 'removed': 1, 'modified': 2, 'unchanged': 2}
    assert stops['Q']['changes'] == [{'field': 'name', 'baseline': 'SYNTHETIC Quarry', 'comparison': 'SYNTHETIC Quarry Gate'}]
    assert stops['S']['changes'][0]['field'] == 'coordinates' and 150 < stops['S']['moved_m'] < 200
    assert stops['OLD']['served_on_date'] == {'baseline': True, 'comparison': None}
    assert r['synthetic'] is True and 'not proof' in r['identity_note'] and r['notices'] == []


def test_out_of_coverage_is_incomparable_not_zero(db):
    con, ids, _ = db
    r = compare(con, ids, service_date=D(hf.ONLY_V2_DATE))
    metric = r['summary']['trip_templates_on_date']
    assert metric == {'baseline': None, 'comparison': 9, 'delta': None, 'percent_change': None,
                      'status': 'incomparable', 'percent_note': 'A side is unavailable for its service date.'}
    assert r['baseline']['date_in_coverage'] is False and 'not zero' in r['baseline']['reason']
    assert {'baseline_date_outside_coverage', 'not_comparable_on_date'} <= {n['code'] for n in r['notices']}
    assert all(x['service']['status'] == 'incomparable' for x in r['routes']['records'])
    # Static record counts remain comparable because they do not depend on a date.
    assert r['summary']['route_records']['status'] == 'unchanged'


def test_calendar_exceptions_and_zero_baselines(db):
    con, ids, _ = db
    # 2026-12-08: v1 removes weekday service (holiday); v2 runs it.
    tue = compare(con, ids, service_date=D('2026-12-08'))['summary']['trip_templates_on_date']
    assert (tue['baseline'], tue['comparison']) == (0, 9) and tue['percent_change'] is None
    assert 'Undefined' in tue['percent_note'] and tue['status'] == 'changed'
    # Sunday 2026-12-27: only v2 adds weekday service by exception.
    sun = compare(con, ids, service_date=D('2026-12-27'))['summary']['trip_templates_on_date']
    assert (sun['baseline'], sun['comparison']) == (0, 9)
    # Saturday: only the Saturday template runs in both.
    sat = compare(con, ids, service_date=D('2026-11-07'))['summary']
    assert (sat['trip_templates_on_date']['baseline'], sat['routes_scheduled']['comparison']) == (1, 1)


def test_overnight_call_belongs_to_its_service_day(db):
    con, ids, _ = db
    r = compare(con, ids)
    night = next(x for x in r['routes']['records'] + compare(con, ids, record_status='unchanged')['routes']['records'] if x['id'] == 'N1')
    assert night['service']['baseline'] == night['service']['comparison'] == 1  # the 24:20 arrival is Friday's service day


def test_different_dates_policy_is_never_called_a_revision(db):
    con, ids, _ = db
    r = compare(con, ids, policy='different_dates', baseline_date=D(hf.ONLY_V1_DATE), comparison_date=D(hf.ONLY_V2_DATE))
    assert r['baseline']['service_date'] == hf.ONLY_V1_DATE and r['comparison']['service_date'] == hf.ONLY_V2_DATE
    notice = next(n for n in r['notices'] if n['code'] == 'different_dates')
    assert 'not attributable to the feed update alone' in notice['text']
    assert r['summary']['trip_templates_on_date']['attributable_to_feed_version'] is False
    assert all(x['service']['attributable_to_feed_version'] is False for x in r['routes']['records'] if x['service'])


def test_equal_snapshots_and_invalid_requests(db):
    con, ids, _ = db
    same = compare(con, ids, b=ids['v1'])
    assert [n['code'] for n in same['notices']] == ['same_snapshot']
    assert same['routes']['counts']['unchanged'] == 6 and same['routes']['matching'] == 0
    with pytest.raises(NotFound):
        compare(con, ids, b='nope')
    with pytest.raises(HistoryError, match='never compared'):
        compare(con, ids, b=ids['other'])
    with pytest.raises(HistoryError, match='same_date requires'):
        compare(con, ids, service_date=None)
    with pytest.raises(HistoryError, match='different_dates requires'):
        compare(con, ids, policy='different_dates', baseline_date=D(hf.COMMON_DATE))
    with pytest.raises(HistoryError, match='Unknown date policy'):
        compare(con, ids, policy='whatever')
    with pytest.raises(HistoryError, match='record_status'):
        compare(con, ids, record_status='bogus')
    with pytest.raises(NotFound):
        SnapshotHistory(con).inventory('no_such_source')


def test_source_isolation_with_shared_ids(db):
    con, ids, _ = db
    r = compare(con, ids, record_status='all')
    p = next(x for x in r['stops']['records'] if x['id'] == 'P')
    assert p['baseline']['name'] == p['comparison']['name'] == 'SYNTHETIC Piazza'
    assert 'Z' not in {x['id'] for x in r['stops']['records']}
    r1 = next(x for x in r['routes']['records'] if x['id'] == 'R1')
    assert r1['status'] == 'unchanged' and r1['baseline']['long_name'] == 'Piazza - Ridge'
    with pytest.raises(HistoryError):
        SnapshotHistory(con).compare(hf.OTHER_SOURCE, ids['v1'], ids['v2'], policy='same_date', service_date=D(hf.COMMON_DATE))


def test_record_paging_and_cache_identity(db):
    con, ids, _ = db
    history = SnapshotHistory(con)
    args = (hf.SOURCE, ids['v1'], ids['v2'])
    first = history.compare(*args, policy='same_date', service_date=D(hf.COMMON_DATE), record_limit=1)
    assert first['routes']['matching'] == 3 and len(first['routes']['records']) == 1
    assert first['routes']['truncated'] and first['routes']['next_offset'] == 1
    rest = history.compare(*args, policy='same_date', service_date=D(hf.COMMON_DATE), record_limit=5, record_offset=1)
    assert [x['id'] for x in rest['routes']['records']] == ['R2', 'R9'] and not rest['routes']['truncated']
    assert history.compare_builds == 1  # paging reuses the cached comparison
    history.compare(*args, policy='same_date', service_date=D('2026-11-09'))
    history.compare(args[0], ids['v2'], ids['v1'], policy='same_date', service_date=D(hf.COMMON_DATE))
    assert history.compare_builds == 3  # date and direction are part of the key
    swapped = history.compare(args[0], ids['v2'], ids['v1'], policy='same_date', service_date=D(hf.COMMON_DATE))
    assert swapped['routes']['counts']['added'] == 1 and swapped['baseline']['snapshot_id'] == ids['v2']


def test_history_api_and_current_atlas_unchanged(db):
    con, ids, tmp = db
    con.close()
    with TestClient(create_app(tmp/'history.sqlite', 'sqlite')) as client:
        before = (client.get('/api/v1/snapshots').json(), client.get(f'/api/v1/summary?date={hf.COMMON_DATE}').json())
        assert [s['snapshot_id'] for s in before[0]] == sorted([ids['other'], ids['v2']], key=lambda i: {ids['other']: hf.OTHER_SOURCE, ids['v2']: hf.SOURCE}[i])
        assert client.get('/api/v1/history/sources').json()['sources'][1]['ready_snapshots'] == 2
        inv = client.get(f'/api/v1/history/{hf.SOURCE}/snapshots?date={hf.COMMON_DATE}').json()
        assert len(inv['snapshots']) == 2
        ok = client.get(f"/api/v1/history/{hf.SOURCE}/compare?baseline={ids['v1']}&comparison={ids['v2']}&date={hf.COMMON_DATE}&record_limit=2")
        assert ok.status_code == 200 and ok.json()['routes']['truncated']
        diff = client.get(f"/api/v1/history/{hf.SOURCE}/compare?baseline={ids['v1']}&comparison={ids['v2']}&policy=different_dates&baseline_date={hf.ONLY_V1_DATE}&comparison_date={hf.ONLY_V2_DATE}")
        assert diff.status_code == 200 and diff.json()['policy'] == 'different_dates'
        assert client.get(f"/api/v1/history/{hf.SOURCE}/compare?baseline={ids['v1']}&comparison={ids['other']}&date={hf.COMMON_DATE}").status_code == 422
        assert client.get(f"/api/v1/history/{hf.SOURCE}/compare?baseline=x&comparison={ids['v2']}&date={hf.COMMON_DATE}").status_code == 404
        assert client.get(f"/api/v1/history/{hf.SOURCE}/compare?baseline={ids['v1']}&comparison={ids['v2']}").status_code == 422
        assert client.get(f"/api/v1/history/{hf.SOURCE}/compare?baseline={ids['v1']}&comparison={ids['v2']}&date={hf.COMMON_DATE}&record_limit=0").status_code == 422
        assert client.get('/api/v1/history/nope/snapshots').status_code == 404
        after = (client.get('/api/v1/snapshots').json(), client.get(f'/api/v1/summary?date={hf.COMMON_DATE}').json())
        assert after == before


@pytest.fixture(scope='module')
def real_db():
    path = ROOT/'data'/'transport_atlas.duckdb'
    if not path.is_file():
        pytest.skip('Local DuckDB with the imported Sardinian feeds is not present')
    try:
        import duckdb
        con = duckdb.connect(str(path), read_only=True)
    except Exception as exc:
        pytest.skip(f'Local DuckDB unavailable ({type(exc).__name__}); stop services to run real-data cases')
    yield con, path
    con.close()


def test_real_database_history_invariants(real_db):
    """One selected publisher-direct snapshot per source; any other snapshot is an unselected,
    ready, hash-verified archived copy (since Phase 7D1: ASPO 396fbbac). Read-only."""
    con, path = real_db
    history = SnapshotHistory(con, validation_folder=ROOT/'data'/'local-verification'/'phase4-validator')
    for source in history.sources():
        inv = history.inventory(source['source_id'], D('2026-11-06'), verify_archives=True)
        selected = [s for s in inv['snapshots'] if s['selected']]
        others = [s for s in inv['snapshots'] if not s['selected']]
        assert len(selected) == 1 and source['comparison_possible'] == (source['ready_snapshots'] >= 2)
        [snap] = selected
        assert snap['archive']['present'] and snap['provenance']['kind'] == 'publisher_direct'
        assert snap['validation'] is not None and snap['validation']['errors'] == 0
        for other in others:
            assert other['import_status'] == 'ready' and other['archive']['sha256_verified']
            assert other['provenance']['kind'] == 'third_party_archived'
        if not others:
            assert 'no version comparison' in inv['note']


# --- Review fixes (post-2dbc80a): policy-scoped cache and partial coordinates ----------

def _policy_view(result):
    return (result['policy'], [n['code'] for n in result['notices']],
            result['summary']['trip_templates_on_date']['attributable_to_feed_version'],
            {x['id']: x['service']['attributable_to_feed_version'] for x in result['routes']['records']})


@pytest.mark.parametrize('first', ['same_date', 'different_dates'])
def test_cache_key_includes_date_policy_for_identical_dates(db, first):
    """same_date(D) and different_dates(D, D) must never share a cached comparison."""
    con, ids, _ = db
    history = SnapshotHistory(con)
    day = D(hf.COMMON_DATE)
    calls = {'same_date': dict(policy='same_date', service_date=day),
             'different_dates': dict(policy='different_dates', baseline_date=day, comparison_date=day)}
    second = 'different_dates' if first == 'same_date' else 'same_date'
    results = {}
    for policy in (first, second, first):
        results.setdefault(policy, []).append(history.compare(hf.SOURCE, ids['v1'], ids['v2'], **calls[policy]))
    assert history.compare_builds == 2  # one build per policy; the repeat is served from cache
    same, different = results['same_date'][0], results['different_dates'][0]
    assert _policy_view(same) == ('same_date', [], True, {'R10': True, 'R2': True, 'R9': True})
    assert _policy_view(different) == ('different_dates', ['different_dates'], False, {'R10': False, 'R2': False, 'R9': False})
    assert _policy_view(results[first][1]) == _policy_view(results[first][0])
    # The counts themselves are identical: only the policy semantics differ.
    assert same['summary']['trip_templates_on_date']['delta'] == different['summary']['trip_templates_on_date']['delta'] == 1


def test_api_policy_switch_with_identical_dates_is_not_served_stale(db):
    con, ids, tmp = db
    con.close()
    base = f"/api/v1/history/{hf.SOURCE}/compare?baseline={ids['v1']}&comparison={ids['v2']}"
    with TestClient(create_app(tmp/'history.sqlite', 'sqlite')) as client:
        same = client.get(f'{base}&policy=same_date&date={hf.COMMON_DATE}').json()
        different = client.get(f'{base}&policy=different_dates&baseline_date={hf.COMMON_DATE}&comparison_date={hf.COMMON_DATE}').json()
        again = client.get(f'{base}&policy=same_date&date={hf.COMMON_DATE}').json()
    assert (same['policy'], different['policy'], again['policy']) == ('same_date', 'different_dates', 'same_date')
    assert 'different_dates' in [n['code'] for n in different['notices']] and again['notices'] == []


def _set_coordinate(con, snapshot_id, stop_id, field, value):
    con.execute(f'UPDATE stops SET {field}=? WHERE snapshot_id=? AND stop_id=?', (value, snapshot_id, stop_id))
    con.commit()


def _stop(con, ids, stop_id):
    result = SnapshotHistory(con).compare(hf.SOURCE, ids['v1'], ids['v2'], policy='same_date',
                                          service_date=D(hf.COMMON_DATE), record_status='all')
    return next(x for x in result['stops']['records'] if x['id'] == stop_id)


@pytest.mark.parametrize('field', ['lat', 'lon'])
def test_coordinate_missing_in_comparison_is_a_change(db, field):
    # Isolated test database only: P is otherwise unchanged between versions.
    con, ids, _ = db
    _set_coordinate(con, ids['v2'], 'P', field, None)
    record = _stop(con, ids, 'P')
    assert record['status'] == 'modified' and record['moved_m'] is None
    [change] = record['changes']
    assert change['field'] == 'coordinates' and 'distance_m' not in change
    assert change['baseline'] == [40.0, 9.0]
    assert change['comparison'] == ([None, 9.0] if field == 'lat' else [40.0, None])


@pytest.mark.parametrize('field', ['lat', 'lon'])
def test_coordinate_missing_in_baseline_is_a_change(db, field):
    con, ids, _ = db
    _set_coordinate(con, ids['v1'], 'P', field, None)
    record = _stop(con, ids, 'P')
    assert record['status'] == 'modified' and record['changes'][0]['comparison'] == [40.0, 9.0]


@pytest.mark.parametrize('field', ['lat', 'lon'])
def test_identical_partial_coordinates_are_unchanged(db, field):
    con, ids, _ = db
    for snapshot in (ids['v1'], ids['v2']):
        _set_coordinate(con, snapshot, 'P', field, None)
    record = _stop(con, ids, 'P')
    assert record['status'] == 'unchanged' and record['changes'] == [] and record['moved_m'] is None


def test_partial_coordinates_differing_in_the_present_value_or_side_are_changes(db):
    con, ids, _ = db
    # Same missing latitude, different longitude: a change, but no distance can be computed.
    _set_coordinate(con, ids['v1'], 'P', 'lat', None)
    _set_coordinate(con, ids['v2'], 'P', 'lat', None)
    _set_coordinate(con, ids['v2'], 'P', 'lon', 9.5)
    record = _stop(con, ids, 'P')
    assert record['status'] == 'modified' and record['changes'][0]['comparison'] == [None, 9.5]
    # Latitude missing on one side, longitude on the other.
    _set_coordinate(con, ids['v1'], 'R', 'lat', None)
    _set_coordinate(con, ids['v2'], 'R', 'lon', None)
    record = _stop(con, ids, 'R')
    assert record['status'] == 'modified' and record['changes'][0]['baseline'] == [None, 9.0]
    assert record['changes'][0]['comparison'] == [40.02, None]
