from datetime import date
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from transport_atlas.api import create_app
from transport_atlas.ingest import import_file
from transport_atlas.network import network_geometry
from transport_atlas.storage import connect
from transport_atlas.timetable import route_timetable

FIX=Path(__file__).parent/'fixtures'

@pytest.fixture
def loaded(tmp_path):
    c=connect(tmp_path/'network.sqlite','sqlite')
    sid=import_file(c,FIX/'synthetic_sardinia.zip',source_id='demo',archive_dir=tmp_path/'raw')['snapshot_id']
    yield c,sid,tmp_path
    c.close()

def test_atlas_all_active_and_shapes(loaded):
    c,sid,_=loaded
    geo=network_geometry(c,date(2026,11,6))
    keys={f['properties']['route_id'] for f in geo['features']}
    assert keys=={'1','E','F'}
    assert geo['metadata']['route_count']==3
    assert not geo['metadata']['truncated']
    assert next(f for f in geo['features'] if f['properties']['route_id']=='1')['properties']['quality']=='source-shape'
    assert all(f['properties']['snapshot_id']==sid for f in geo['features'])

def test_atlas_inactive_and_filters(loaded):
    c,_,_=loaded
    all_geo=network_geometry(c,date(2026,11,6),active_only=False)
    assert {f['properties']['route_id'] for f in all_geo['features']}=={'1','E','F','S'}
    assert network_geometry(c,date(2026,11,6),mode='rail')['features']==[]
    assert network_geometry(c,date(2026,11,6),source_id='not_a_source')['features']==[]
    assert network_geometry(c,date(2026,11,6),max_routes=2)['metadata']['truncated'] is True
    assert network_geometry(c,date(2026,11,6),max_points=2)['metadata']['truncated'] is True

def test_atlas_preserves_overlapping_ids(loaded):
    c,_,root=loaded
    import_file(c,FIX/'synthetic_second_source.zip',source_id='second',archive_dir=root/'raw')
    geo=network_geometry(c,date(2026,11,6))
    uids={f['properties']['uid'] for f in geo['features']}
    assert len(uids)==4
    assert geo['metadata']['source_count']==2


def test_adaptive_budget_continues_after_a_point_count_plateau(loaded):
    c,sid,_=loaded
    c.execute('DELETE FROM shapes WHERE snapshot_id=?',(sid,))
    c.executemany('INSERT INTO shapes VALUES(?,?,?,?,?)',[
        (sid,'SH1',i,40+(0.0003 if i%2 else 0),9+i*0.001) for i in range(21)])
    geo=network_geometry(c,date(2026,11,6),max_points=8)
    assert not geo['metadata']['truncated']
    assert {f['properties']['route_id'] for f in geo['features']}=={'1','E','F'}
    assert geo['metadata']['point_count']<=8

def test_timetable_exact_vs_frequency_and_overnight(loaded):
    c,sid,_=loaded
    exact=route_timetable(c,sid,'E',date(2026,11,6))
    assert [x['departure_local'][11:16] for x in exact['departures']]==['08:00','08:20','08:40']
    assert exact['frequency_windows']==[]
    freq=route_timetable(c,sid,'F',date(2026,11,6))
    assert freq['departures']==[] and len(freq['frequency_windows'])==1
    night=route_timetable(c,sid,'1',date(2026,11,6))
    assert night['departures'][0]['departure_local'].startswith('2026-11-06T23:50')
    assert night['departures'][0]['arrival_local'].startswith('2026-11-07T01:15')
    assert route_timetable(c,sid,'E',date(2026,11,6),limit=2)['truncated'] is True
    with pytest.raises(KeyError): route_timetable(c,sid,'bad',date(2026,11,6))

def test_new_api_contract(loaded):
    c,sid,root=loaded
    c.close()
    with TestClient(create_app(root/'network.sqlite','sqlite')) as client:
        r=client.get('/api/v1/network/geometry?date=2026-11-06&active_only=true')
        assert r.status_code==200 and r.json()['metadata']['feature_count']==3
        assert client.get('/api/v1/network/geometry?date=2026-11-06&mode=space').status_code==422
        r=client.get(f'/api/v1/route/{sid}/E/timetable?date=2026-11-06')
        assert r.status_code==200 and r.json()['total_departures']==3
        assert client.get(f'/api/v1/route/{sid}/missing/timetable?date=2026-11-06').status_code==404
