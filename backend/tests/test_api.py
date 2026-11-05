from datetime import date
from fastapi.testclient import TestClient
from transport_atlas.api import create_app
from transport_atlas.ingest import import_file
from transport_atlas.storage import connect
from pathlib import Path

FIX=Path(__file__).parent/'fixtures'/'synthetic_sardinia.zip'

def test_api_contract(tmp_path):
    db=tmp_path/'api.sqlite'
    c=connect(db,'sqlite')
    result=import_file(c,FIX,source_id='arst',archive_dir=tmp_path/'raw')
    sid=result['snapshot_id']
    c.close()
    with TestClient(create_app(db,'sqlite')) as client:
        assert client.get('/api/v1/health').json()['status']=='ok'
        snaps=client.get('/api/v1/snapshots').json()
        assert len(snaps)==1 and snaps[0]['snapshot_id']==sid
        assert client.get('/api/v1/summary?date=2026-11-06').json()['snapshots'][0]['scheduled_trip_instances']==4
        rr=client.get('/api/v1/routes?date=2026-11-06').json()['routes']
        assert len(rr)==4
        assert [r for r in rr if r['route_id']=='S'][0]['active_on_date'] is False
        geometry=client.get(f'/api/v1/route/{sid}/1/geometry').json()
        assert len(geometry['features'])==1
        assert geometry['features'][0]['properties']['quality']=='source-shape'
        fallback=client.get(f'/api/v1/route/{sid}/E/geometry').json()
        assert fallback['features'][0]['properties']['quality']=='schematic-stop-link'
        board=client.get(f'/api/v1/stop/{sid}/B/departures?date=2026-11-07').json()
        assert any(row['gtfs_time']=='25:15:00' for row in board['departures'])
        assert client.get(f'/api/v1/stop/{sid}/unknown/departures?date=2026-11-07').status_code==404
        assert client.get(f'/api/v1/trip/{sid}/T_NIGHT').json()['stops'][1]['departure_sec']==90900
