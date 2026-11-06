from datetime import date
from pathlib import Path
import json
import hashlib

from fastapi.testclient import TestClient
import pytest

from transport_atlas.api import create_app
from transport_atlas.ingest import import_file
from transport_atlas.network import mode_for_type
from transport_atlas.storage import connect, one
from transport_atlas.timetable import route_timetable
from transport_atlas.official import _opener

FIX = Path(__file__).parent / 'fixtures' / 'synthetic_sardinia.zip'


def test_official_opener_verifies_certificates():
    import ssl
    from urllib.request import HTTPSHandler
    handler = next(h for h in _opener().handlers if isinstance(h, HTTPSHandler))
    assert handler._context is not None
    assert handler._context.verify_mode == ssl.CERT_REQUIRED
    assert handler._context.check_hostname
    import sys
    if sys.platform == 'win32':
        import truststore
        assert isinstance(handler._context, truststore.SSLContext)


def test_tram_is_distinct_from_rail():
    assert mode_for_type(0) == 'tram'
    assert mode_for_type(2) == 'rail'


def test_geometry_simplification_preserves_bends():
    from transport_atlas.network import simplify_line
    points = [[9+i/10000,40] for i in range(1000)] + [[9.0999,40.01]]
    simplified = simplify_line(points, 0.0001)
    assert simplified == [points[0], points[-2], points[-1]]
    assert simplify_line(points, 0) == points


@pytest.mark.parametrize('backend', ['duckdb', 'sqlite'])
def test_bulk_import_retains_null_empty_unicode_and_transaction(tmp_path, backend):
    from transport_atlas.storage import insert_many
    c = connect(tmp_path / "quoted'db", backend)
    values = [('snapshot', str(i), 'Fermata "Olbia",\nCittà' if i % 2 else '',
               40.5, 9.5, 0, None, '') for i in range(1200)]
    try:
        c.execute('BEGIN TRANSACTION')
        insert_many(c, 'stops', 'INSERT INTO stops VALUES(?,?,?,?,?,?,?,?)', values)
        actual = c.execute('SELECT * FROM stops ORDER BY CAST(stop_id AS INTEGER)').fetchall()
        assert [tuple(r) for r in actual] == values
        c.rollback()
        assert one(c, 'SELECT COUNT(*) AS n FROM stops')['n'] == 0
    finally:
        c.close()


def test_import_preserves_verified_download_provenance(tmp_path):
    feed = tmp_path / 'feed.zip'
    feed.write_bytes(FIX.read_bytes())
    meta = {'source_id': 'demo', 'sha256': hashlib.sha256(feed.read_bytes()).hexdigest(),
            'retrieved_at': '2026-11-01T12:00:00+00:00',
            'download_url': 'https://www.sardegnamobilita.it/example.zip'}
    feed.with_suffix('.zip.provenance.json').write_text(json.dumps(meta))
    c = connect(tmp_path / 'db.sqlite', 'sqlite')
    try:
        result = import_file(c, feed, source_id='demo', archive_dir=tmp_path / 'raw')
        assert one(c, 'SELECT retrieved_at FROM feed_snapshots')['retrieved_at'] == meta['retrieved_at']
        assert one(c, 'SELECT download_url FROM feed_sources')['download_url'] == meta['download_url']
        archive = Path(one(c, 'SELECT raw_zip_path FROM feed_snapshots')['raw_zip_path'])
        assert json.loads(archive.with_suffix('.zip.provenance.json').read_text()) == meta
        assert result['status'] == 'imported'
    finally:
        c.close()


def test_mismatched_provenance_cannot_be_imported(tmp_path):
    feed = tmp_path / 'feed.zip'
    feed.write_bytes(FIX.read_bytes())
    feed.with_suffix('.zip.provenance.json').write_text(json.dumps({'sha256': 'wrong', 'source_id': 'demo'}))
    c = connect(tmp_path / 'db.sqlite', 'sqlite')
    try:
        with pytest.raises(ValueError, match='provenance'):
            import_file(c, feed, source_id='demo', archive_dir=tmp_path / 'raw')
        assert one(c, 'SELECT COUNT(*) AS n FROM feed_snapshots')['n'] == 0
    finally:
        c.close()


def test_historical_snapshot_keeps_its_download_url(tmp_path):
    from transport_atlas.route_details import route_evidence
    c = connect(tmp_path / 'db.sqlite', 'sqlite')
    ids = []
    try:
        for fixture, suffix in [(FIX, 'first'), (FIX.parent/'synthetic_second_source.zip', 'second')]:
            feed = tmp_path / (suffix+'.zip')
            feed.write_bytes(fixture.read_bytes())
            meta = {'source_id':'demo', 'sha256':hashlib.sha256(feed.read_bytes()).hexdigest(),
                    'retrieved_at':'2026-11-01T12:00:00+00:00',
                    'download_url':f'https://www.sardegnamobilita.it/{suffix}.zip'}
            feed.with_suffix('.zip.provenance.json').write_text(json.dumps(meta))
            ids.append(import_file(c, feed, source_id='demo', archive_dir=tmp_path/'raw')['snapshot_id'])
        assert route_evidence(c, ids[0], 'E')['provenance']['download_url'].endswith('/first.zip')
        assert route_evidence(c, ids[1], '1')['provenance']['download_url'].endswith('/second.zip')
    finally:
        c.close()


def test_geometry_cache_follows_snapshot_selection(tmp_path):
    db = tmp_path / 'cache.duckdb'
    c = connect(db)
    import_file(c, FIX, source_id='demo', archive_dir=tmp_path/'raw')
    c.close()
    app = create_app(db, 'duckdb')
    with TestClient(app) as client:
        first = client.get('/api/v1/network/geometry?date=2026-11-06').json()
        import_file(app.state.con, FIX.parent/'synthetic_second_source.zip', source_id='second', archive_dir=tmp_path/'raw')
        second = client.get('/api/v1/network/geometry?date=2026-11-06').json()
        assert first['metadata']['source_count'] == 1
        assert second['metadata']['source_count'] == 2
        assert len(second['features']) > len(first['features'])


def test_persistent_geometry_cache_recovers_from_corruption(tmp_path):
    db = tmp_path/'cache.duckdb'
    c = connect(db)
    import_file(c, FIX, source_id='demo', archive_dir=tmp_path/'raw')
    c.close()
    with TestClient(create_app(db,'duckdb')) as client:
        expected = client.get('/api/v1/network/geometry?date=2026-11-06').json()
    files = list((tmp_path/'cache'/'network').glob('*.json'))
    assert len(files) == 1
    files[0].write_text('incomplete cache file')
    with TestClient(create_app(db,'duckdb')) as client:
        assert client.get('/api/v1/network/geometry?date=2026-11-06').json() == expected


def test_declared_feed_validity_is_distinct_from_calendar_coverage(tmp_path):
    from zipfile import ZipFile
    from transport_atlas.schedule import daily_summary
    from transport_atlas.route_details import route_evidence
    feed = tmp_path/'declared.zip'
    with ZipFile(FIX) as original, ZipFile(feed, 'w') as target:
        for name in original.namelist():
            target.writestr(name, original.read(name))
        target.writestr('feed_info.txt', 'feed_publisher_name,feed_publisher_url,feed_lang,feed_start_date,feed_end_date\nSynthetic,https://example.invalid,it,20261110,20261130\n')
    c = connect(tmp_path/'declared.sqlite', 'sqlite')
    try:
        sid = import_file(c, feed, source_id='demo', archive_dir=tmp_path/'raw')['snapshot_id']
        summary = daily_summary(c, date(2026,11,6))['snapshots'][0]
        assert summary['feed_start'] == '2026-11-01'
        assert summary['declared_feed_start'] == '2026-11-10'
        assert not summary['service_date_covered']
        assert route_evidence(c, sid, 'E')['provenance']['feed_info']['feed_start_date'] == '20261110'
    finally:
        c.close()


def test_scoped_stops_and_complete_timetable_details(tmp_path):
    db = tmp_path / 'db.duckdb'
    c = connect(db)
    sid = import_file(c, FIX, source_id='demo', archive_dir=tmp_path / 'raw')['snapshot_id']
    exact = route_timetable(c, sid, 'E', date(2026, 11, 6), limit=1)
    assert exact['first_departure'][11:16] == '08:00'
    assert exact['last_departure'][11:16] == '08:40'
    assert exact['variants'][0]['scheduled_departures'] == 3
    assert exact['variants'][0]['median_interval_minutes'] == 20
    assert exact['provenance']['source_id'] == 'demo'
    assert exact['calendars']
    c.close()
    with TestClient(create_app(db, 'duckdb')) as client:
        assert client.get('/api/v1/stops?source=absent').json()['stops'] == []
        assert client.get('/api/v1/stops?sources=absent').json()['stops'] == []
        assert client.get('/api/v1/stops?search=does-not-exist').json()['stops'] == []
        assert client.get('/api/v1/network/geometry?date=2026-11-06&sources=absent').json()['features'] == []
        assert client.get('/api/v1/stops?mode=rail').json()['stops'] == []
        r = client.get('/api/v1/stops?date=2026-11-06&active_only=true&limit=1').json()
        assert len(r['stops']) == 1 and r['truncated']
        assert r['total'] > 1
        assert client.get('/api/v1/network/geometry?date=2026-11-06&mode=tram').status_code == 200
        geometry = client.get('/api/v1/network/geometry?date=2026-11-06').json()
        assert all(f['properties']['direction_ids'] == ['null'] for f in geometry['features'])
        # Resolve a real trip ID from the complete variant response, not an assumed ID.
        tid = exact['variants'][0]['representative_trip_id']
        trip = client.get(f'/api/v1/trip/{sid}/{tid}').json()
        assert len(trip['stops']) >= 2
        assert trip['geometry']['features'][0]['properties']['quality'] in {'source-shape', 'schematic-stop-link'}
