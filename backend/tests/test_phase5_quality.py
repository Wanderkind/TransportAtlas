"""Phase 5 data-quality indicators: intervals, stop status, freshness, validation."""
from datetime import date
import json
from pathlib import Path
import zipfile
import pytest
from fastapi.testclient import TestClient
from transport_atlas import quality
from transport_atlas.api import create_app
from transport_atlas.ingest import import_file
from transport_atlas.storage import connect, one

FIX = Path(__file__).parent/'fixtures'/'synthetic_sardinia.zip'
ROOT = Path(__file__).resolve().parents[2]
FRIDAY = date(2026, 11, 6)


def stop(seq, t_arr, t_dep=None, lat=40.0, lon=9.0, stop_id=None):
    return {'stop_sequence': seq, 'stop_id': stop_id or f'S{seq}', 'arrival_sec': t_arr,
            'departure_sec': t_arr if t_dep is None else t_dep, 'lat': lat, 'lon': lon, 'name': f'Stop {seq}'}


def hms(h, m, s=0):
    return h*3600+m*60+s


NORTH_366_M = 366/111_195  # degrees of latitude for ~366 m


# --- interval rules --------------------------------------------------------------

def test_one_second_spacing_over_366_m_is_implausible_not_proven():
    result = quality.segment_intervals([stop(4, hms(9, 39)), stop(5, hms(9, 39, 1), lat=40+NORTH_366_M)], 3)
    [finding] = result['findings']
    assert finding['classification'] == 'implausible' and finding['reason'] == 'min_implied_speed_exceeds_threshold'
    assert finding['timing_resolution_seconds'] == 1 and finding['straight_line_m'] == pytest.approx(366, abs=1)
    assert finding['min_implied_speed_kph'] == pytest.approx(366/2*3.6, rel=0.01)
    assert result['distance_basis'] == 'straight_line' and result['threshold_kph'] == 150


def test_near_zero_rule_needs_second_precision_and_distance():
    near = [stop(1, hms(9, 0, 1)), stop(2, hms(9, 0, 2), lat=40+60/111_195)]
    assert quality.segment_intervals(near, 3)['findings'][0]['reason'] == 'near_zero_interval'
    adjacent = [stop(1, hms(9, 0, 1)), stop(2, hms(9, 0, 2), lat=40+20/111_195)]
    assert quality.segment_intervals(adjacent, 3)['findings'] == []


def test_rounded_times_get_their_rounding_step_as_slack():
    # ARST-style equal half-minute times ~1 km apart: up to 30 s could have passed.
    half = [stop(1, hms(8, 54, 30)), stop(2, hms(8, 54, 30), lat=40+995/111_195)]
    assert quality.segment_intervals(half, 3)['findings'] == []
    # Same minute, 3 km apart: even a 60 s gap implies 180 km/h for a bus.
    far = [stop(1, hms(8, 54)), stop(2, hms(8, 54), lat=40+3000/111_195)]
    [finding] = quality.segment_intervals(far, 3)['findings']
    assert finding['timing_resolution_seconds'] == 60
    # Rail has a higher threshold (500 km/h), so the same pair is not flagged.
    assert quality.segment_intervals(far, 2)['findings'] == []


def test_overnight_service_day_times_are_not_a_wrap():
    overnight = [stop(1, hms(23, 59)), stop(2, hms(24, 1), lat=40.01)]
    assert quality.segment_intervals(overnight, 3)['findings'] == []
    wrapped = [stop(1, hms(23, 59)), stop(2, hms(0, 1), lat=40.01)]
    [finding] = quality.segment_intervals(wrapped, 3)['findings']
    assert finding['classification'] == 'inconsistent' and finding['reason'] == 'time_decreases'
    assert finding['from_time'] == '23:59:00' and finding['to_time'] == '00:01:00'
    assert quality.clock(hms(25, 15)) == '25:15:00'


def test_departure_before_arrival_untimed_and_missing_coordinates():
    rows = [stop(1, hms(9, 5), hms(9, 4)), {**stop(2, None), 'departure_sec': None},
            stop(3, hms(9, 10), lat=None), stop(4, hms(9, 12))]
    result = quality.segment_intervals(rows, 3)
    assert [f['reason'] for f in result['findings']] == ['departure_before_arrival']
    assert result['segments_untimed'] == 2 and result['segments_without_coordinates'] == 1
    assert result['segments_checked'] == 1


def test_values_are_reported_unchanged():
    rows = [stop(1, hms(9, 39)), stop(2, hms(9, 39, 1), lat=40+NORTH_366_M)]
    before = json.dumps(rows)
    quality.segment_intervals(rows, 3)
    assert json.dumps(rows) == before


def test_version_label_dates_are_only_read_when_explicit():
    assert quality.version_date('GTFS passa per Sardegna_20260605_092435') == '2026-06-05'
    assert quality.version_date('01112026') is None  # day-first label: not guessed
    assert quality.version_date('1') is None and quality.version_date(None) is None
    assert quality.version_date('v20261340') is None


# --- database fixtures -------------------------------------------------------------

@pytest.fixture
def feed(tmp_path):
    con = connect(tmp_path/'q.sqlite', 'sqlite')
    sid = import_file(con, FIX, source_id='demo', archive_dir=tmp_path/'raw')['snapshot_id']
    con.execute('INSERT INTO stops(snapshot_id,stop_id,name,lat,lon,location_type) VALUES(?,?,?,?,?,?)', (sid, 'U', 'Unused', 40.7201, 8.56, 0))
    con.execute('INSERT INTO stops(snapshot_id,stop_id,name,lat,lon,location_type) VALUES(?,?,?,?,?,?)', (sid, 'ST', 'Station', 40.7202, 8.56, 1))
    con.commit()
    snapshot = one(con, 'SELECT * FROM feed_snapshots WHERE snapshot_id=?', (sid,))
    yield con, sid, snapshot, tmp_path
    con.close()


def test_stop_status_classes_and_precision(feed):
    con, sid, snapshot, _ = feed
    status = quality.stop_service_status(con, snapshot, ['A', 'U', 'ST'], FRIDAY)
    a = status['A']
    assert a['status'] == 'scheduled' and a['trip_templates'] == 4
    # T_NIGHT and T_EXACT's 3 exact headway departures are exact; T_FREQ is a window only.
    assert a['active_trip_templates'] == 3 and a['exact_calls_on_date'] == 1+3 and a['frequency_templates_on_date'] == 1
    assert status['U']['status'] == 'no_trip_references'
    assert status['ST']['status'] == 'unknown' and 'platform' in status['ST']['reason']
    # Thursday: inside calendar coverage, WEEK runs Fridays only.
    assert quality.stop_service_status(con, snapshot, ['A'], date(2026, 11, 5))['A']['status'] == 'not_scheduled_on_date'
    # Exception removes Friday 13th.
    assert quality.stop_service_status(con, snapshot, ['A'], date(2026, 11, 13))['A']['status'] == 'not_scheduled_on_date'
    # Outside the calendars nothing can be established.
    outside = quality.stop_service_status(con, snapshot, ['A'], date(2027, 3, 5))['A']
    assert outside['status'] == 'unknown' and 'coverage' in outside['reason']


def test_daily_volume_follows_calendars_and_exceptions(feed):
    con, sid, snapshot, _ = feed
    quality._VOLUME_CACHE.pop(sid, None)
    volume = quality.daily_trip_volume(con, snapshot)
    assert volume['2026-11-06'] == 3 and volume['2026-11-13'] == 0 and volume['2026-11-07'] == 1
    assert volume['2026-11-05'] == 0


def _zip_with(tmp_path, feed_info: str | None):
    path = tmp_path/'variant.zip'
    with zipfile.ZipFile(FIX) as source, zipfile.ZipFile(path, 'w') as target:
        for name in source.namelist():
            target.writestr(name, source.read(name))
        if feed_info is not None:
            target.writestr('feed_info.txt', feed_info)
    return path


def test_source_freshness_never_uses_retrieval_as_publication(feed):
    con, sid, snapshot, tmp_path = feed
    result = quality.source_quality(con, dict(snapshot, raw_zip_path=str(_zip_with(tmp_path, None))), FRIDAY)
    fresh = result['freshness']
    assert fresh['retrieved_at'] == snapshot['retrieved_at'] and fresh['publication_date'] is None
    assert fresh['feed_info_present'] is False and fresh['feed_version'] is None
    assert fresh['observed_start'] == snapshot['feed_start'] and fresh['in_declared_validity'] is None
    codes = [i['code'] for i in result['indicators']]
    assert 'no_feed_info' in codes and 'external_validation_missing' in codes
    assert 'long_range_reduced_volume' not in codes


def test_projection_caution_needs_dated_label_age_and_reduced_volume(feed, tmp_path):
    con, sid, snapshot, _ = feed
    # Add a busier Friday so Nov 6 has <= 75% of the busiest Friday's trips.
    con.execute('INSERT INTO calendar_exceptions VALUES(?,?,?,?)', (sid, 'SCHOOL', '2026-11-20', 1))
    con.commit()
    quality._VOLUME_CACHE.pop(sid, None)
    info = 'feed_publisher_name,feed_publisher_url,feed_lang,feed_start_date,feed_end_date,feed_version\nX,https://x.invalid,it,20261101,20261130,{}\n'
    old = dict(snapshot, raw_zip_path=str(_zip_with(tmp_path, info.format('build_20260605'))))
    result = quality.source_quality(con, old, FRIDAY)
    caution = next(i for i in result['indicators'] if i['code'] == 'long_range_reduced_volume')
    assert caution['category'] == 'coverage' and caution['level'] == 'caution'
    assert 'does not show missing service' in caution['detail'] and '2026-06-05' in caution['detail']
    assert result['freshness']['version_label_date'] == '2026-06-05'
    assert result['freshness']['busiest_same_weekday'] == {'date': '2026-11-20', 'trip_templates': 4}
    # A recent label, or no dated label, gives no caution: seasonal volume alone is not alarming.
    (tmp_path/'r').mkdir(exist_ok=True)
    recent = dict(snapshot, raw_zip_path=str(_zip_with(tmp_path/'r', info.format('build_20261020'))))
    assert 'long_range_reduced_volume' not in [i['code'] for i in quality.source_quality(con, recent, FRIDAY)['indicators']]
    (tmp_path/'u').mkdir(exist_ok=True)  # metadata is cached per archive path + digest
    undated = dict(snapshot, raw_zip_path=str(_zip_with(tmp_path/'u', info.format('7'))))
    assert 'long_range_reduced_volume' not in [i['code'] for i in quality.source_quality(con, undated, FRIDAY)['indicators']]


def test_date_outside_calendars_is_not_called_absent(feed):
    con, sid, snapshot, _ = feed
    codes = [i['code'] for i in quality.source_quality(con, snapshot, date(2027, 3, 5))['indicators']]
    assert 'date_outside_calendar' in codes and 'no_service_on_date' not in codes


def test_validation_record_matches_exact_archive_only(tmp_path):
    def run(name, sha, *, located=True, status='report_generated', stamp='2026-11-07T02:00:00+00:00'):
        folder = tmp_path/name
        folder.mkdir()
        (folder/'report.json').write_text(json.dumps({'notices': [
            {'code': 'fast_travel_between_consecutive_stops', 'severity': 'WARNING', 'totalNotices': 329},
            {'code': 'unknown_column', 'severity': 'INFO', 'totalNotices': 1}]}), encoding='utf-8')
        (folder/'evidence.json').write_text(json.dumps({'status': status, 'input_sha256': sha, 'validator_version': '8.0.1',
            'date': '2026-11-06', 'validated_at_utc': stamp,
            'report_directory': str(folder if located else tmp_path/'elsewhere')}), encoding='utf-8')
    run('good', 'a'*64)
    run('other', 'b'*64, stamp='2026-11-08T00:00:00+00:00')
    run('misplaced', 'a'*64, located=False, stamp='2026-11-09T00:00:00+00:00')
    run('failed', 'a'*64, status='validator_failed', stamp='2026-11-10T00:00:00+00:00')
    record = quality.validation_record(tmp_path, 'a'*64)
    assert record['errors'] == 0 and record['warnings'] == 329 and record['infos'] == 1
    assert record['validated_at_utc'].startswith('2026-11-07')
    assert quality.validation_record(tmp_path, 'c'*64) is None


def test_quality_api_endpoints(feed):
    con, sid, snapshot, root = feed
    con.close()
    with TestClient(create_app(root/'q.sqlite', 'sqlite')) as client:
        near = client.get(f'/api/v1/stop/{sid}/A/nearby?radius=200&date=2026-11-06').json()
        assert near['origin']['service']['status'] == 'scheduled'
        assert {s['stop_id']: s['service']['status'] for s in near['stops']} == {'U': 'no_trip_references', 'ST': 'unknown'}
        assert 'service' not in client.get(f'/api/v1/stop/{sid}/A/nearby?radius=200').json()['stops'][0]
        stop_q = client.get(f'/api/v1/quality/stop/{sid}/U?date=2026-11-06').json()
        assert [i['code'] for i in stop_q['indicators']] == ['stop_without_trips']
        route_q = client.get(f'/api/v1/quality/route/{sid}/E').json()
        assert route_q['timing']['implausible_segments'] == 0
        assert [i['code'] for i in route_q['indicators']] == ['schematic_trips']
        source_q = client.get(f'/api/v1/quality/source/{sid}?date=2026-11-06').json()
        assert source_q['freshness']['service_date'] == '2026-11-06'
        trip = client.get(f'/api/v1/trip/{sid}/T_NIGHT').json()
        assert trip['timing']['findings'] == [] and trip['timing']['frequency_based'] is False
        assert client.get(f'/api/v1/trip/{sid}/T_FREQ').json()['timing']['frequency_based'] is True
        assert client.get(f'/api/v1/quality/route/{sid}/missing').status_code == 404
        assert client.get(f'/api/v1/quality/stop/{sid}/missing?date=2026-11-06').status_code == 404
        assert client.get('/api/v1/quality/source/missing?date=2026-11-06').status_code == 404


# --- known real-feed cases (skipped when the local database is unavailable) ---------

@pytest.fixture(scope='module')
def real():
    path = ROOT/'data'/'transport_atlas.duckdb'
    if not path.is_file():
        pytest.skip('Local DuckDB with the imported Sardinian feeds is not present')
    try:
        import duckdb
        con = duckdb.connect(str(path), read_only=True)
    except Exception as exc:  # e.g. locked by a running backend
        pytest.skip(f'Local DuckDB unavailable ({type(exc).__name__}); stop services to run real-feed cases')
    snapshots = {r[0]: dict(zip(('source_id', 'snapshot_id', 'content_sha256', 'raw_zip_path', 'feed_start', 'feed_end', 'retrieved_at'), r))
                 for r in con.execute('''SELECT s.source_id,s.snapshot_id,s.content_sha256,s.raw_zip_path,s.feed_start,
                     s.feed_end,s.retrieved_at FROM feed_snapshots s JOIN selection_members m USING(snapshot_id)''').fetchall()}
    yield con, snapshots
    con.close()


def test_known_aspo_one_second_interval(real):
    con, snaps = real
    sid = snaps['aspo']['snapshot_id']
    rows = quality._trip_rows(con, sid, 't.trip_id=?', ('216987',))['216987']
    finding = next(f for f in quality.segment_intervals(rows, 3)['findings'] if f['from_stop_id'] == '873')
    assert (finding['to_stop_id'], finding['from_time'], finding['to_time']) == ('871', '09:39:00', '09:39:01')
    assert finding['min_implied_speed_kph'] > 600 and finding['classification'] == 'implausible'
    summary = quality.timing_summary(con, sid)
    assert summary['implausible_segments'] == 310 and summary['trip_templates_with_implausible'] == 67
    assert summary['trip_templates_checked'] == 394 and summary['inconsistent_segments'] == 0


def test_known_arst_half_minute_times_are_not_flagged(real):
    con, snaps = real
    rows = quality._trip_rows(con, snaps['arst']['snapshot_id'], 't.trip_id=?', ('827_3',))['827_3']
    assert quality.segment_intervals(rows, 3)['findings'] == []


def test_known_unused_stop_records(real):
    con, snaps = real
    for source, expected in (('atp_sassari', 115), ('arst', 6), ('aspo', 0), ('trenitalia_sardegna', 0)):
        snap = snaps[source]
        ids = [r[0] for r in con.execute('SELECT stop_id FROM stops WHERE snapshot_id=?', [snap['snapshot_id']]).fetchall()]
        status = quality.stop_service_status(con, snap, ids, FRIDAY)
        assert sum(s['status'] == 'no_trip_references' for s in status.values()) == expected


def test_known_trenitalia_projection_caution(real):
    con, snaps = real
    snap = dict(snaps['trenitalia_sardegna'], raw_zip_path=str(ROOT/snaps['trenitalia_sardegna']['raw_zip_path']))
    nov = quality.source_quality(con, snap, FRIDAY)
    assert nov['freshness']['trip_templates_on_date'] == 200
    assert nov['freshness']['version_label_date'] == '2026-06-05'
    assert nov['freshness']['retrieved_at'].startswith('2026-11-06')
    assert 'long_range_reduced_volume' in [i['code'] for i in nov['indicators']]
    june = quality.source_quality(con, snap, date(2026, 6, 12))
    assert 'long_range_reduced_volume' not in [i['code'] for i in june['indicators']]
