"""Cross-source proximity is exact, bounded and clearly not a stop match."""
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from transport_atlas.api import create_app
from transport_atlas.ingest import import_file
from transport_atlas.nearby import distance_m, nearby_stops
from transport_atlas.storage import connect

FIX = Path(__file__).parent/'fixtures'/'synthetic_sardinia.zip'


@pytest.fixture
def two_sources(tmp_path):
    con = connect(tmp_path/'near.sqlite', 'sqlite')
    first = import_file(con, FIX, source_id='first', archive_dir=tmp_path/'raw')['snapshot_id']
    second = import_file(con, FIX, source_id='second', archive_dir=tmp_path/'raw2')['snapshot_id']
    # A same-source stop ~111 m north of A, and one just beyond 200 m.
    con.execute('INSERT INTO stops(snapshot_id,stop_id,name,lat,lon) VALUES(?,?,?,?,?)', (first, 'A2', 'North of A', 40.721, 8.56))
    con.execute('INSERT INTO stops(snapshot_id,stop_id,name,lat,lon) VALUES(?,?,?,?,?)', (first, 'A3', 'Far north of A', 40.7219, 8.56))
    con.execute('INSERT INTO stops(snapshot_id,stop_id,name,lat,lon) VALUES(?,?,?,?,?)', (first, 'NOXY', 'No coordinates', None, None))
    con.commit()
    yield con, first, second, tmp_path
    con.close()


def test_distance_is_haversine():
    assert distance_m(40.72, 8.56, 40.72, 8.56) == 0
    assert distance_m(40.72, 8.56, 40.721, 8.56) == pytest.approx(111.19, abs=0.05)


def test_nearby_spans_sources_sorted_and_excludes_origin(two_sources):
    con, first, second, _ = two_sources
    result = nearby_stops(con, first, 'A', radius_m=200)
    assert [(s['source_id'], s['stop_id']) for s in result['stops']] == [('second', 'A'), ('first', 'A2')]
    assert result['stops'][0]['distance_m'] == 0 and not result['stops'][0]['same_source']
    assert result['stops'][1]['same_source'] and result['total'] == 2 and not result['truncated']
    assert 'Not a verified' in result['note']
    assert nearby_stops(con, first, 'A', radius_m=250)['total'] == 3
    assert nearby_stops(con, first, 'A', radius_m=250, limit=1)['truncated']
    assert nearby_stops(con, first, 'NOXY')['stops'] == []
    with pytest.raises(KeyError):
        nearby_stops(con, first, 'missing')


def test_nearby_api(two_sources):
    con, first, _, root = two_sources
    con.close()
    with TestClient(create_app(root/'near.sqlite', 'sqlite')) as client:
        assert client.get(f'/api/v1/stop/{first}/A/nearby?radius=200').json()['total'] == 2
        assert client.get(f'/api/v1/stop/{first}/A/nearby?radius=5000').status_code == 422
        assert client.get(f'/api/v1/stop/{first}/missing/nearby').status_code == 404
