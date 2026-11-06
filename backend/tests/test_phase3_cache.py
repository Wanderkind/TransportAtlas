from concurrent.futures import ThreadPoolExecutor,TimeoutError
from pathlib import Path
from threading import Event
from fastapi.testclient import TestClient
import transport_atlas.api as api
from transport_atlas.ingest import import_file
from transport_atlas.storage import connect


def test_cached_view_is_not_blocked_by_an_unrelated_slow_build(tmp_path,monkeypatch):
    db=tmp_path/'cache.sqlite'
    con=connect(db,'sqlite')
    import_file(con,Path(__file__).parent/'fixtures'/'synthetic_sardinia.zip',source_id='demo',archive_dir=tmp_path/'raw')
    con.close()
    started,release=Event(),Event()
    original=api.network_geometry
    def controlled(con,day,**kwargs):
        if kwargs.get('mode')=='bus':
            started.set()
            assert release.wait(10),'Test did not release the slow request'
        return original(con,day,**kwargs)
    monkeypatch.setattr(api,'network_geometry',controlled)
    with TestClient(api.create_app(db,'sqlite')) as client,ThreadPoolExecutor(max_workers=2) as pool:
        assert client.get('/api/v1/network/geometry?date=2026-11-06').status_code==200
        slow=pool.submit(client.get,'/api/v1/network/geometry?date=2026-11-06&mode=bus')
        assert started.wait(5)
        fast=pool.submit(client.get,'/api/v1/network/geometry?date=2026-11-06')
        try:
            try:response=fast.result(timeout=1)
            except TimeoutError:raise AssertionError('A cached view waited behind a different cold request')
            assert response.status_code==200
        finally:
            release.set()
        assert slow.result(timeout=5).status_code==200
