"""Persisted simplified shape levels must reproduce geometry exactly and fail safe."""
from datetime import date
from pathlib import Path
import json
from transport_atlas import shape_store
from transport_atlas.ingest import import_file
from transport_atlas.network import network_geometry, SHAPE_CACHE_LIMIT
from transport_atlas.schedule import active_snapshots
from transport_atlas.storage import connect


def _loaded(tmp_path):
    con = connect(tmp_path/'store.sqlite', 'sqlite')
    import_file(con, Path(__file__).parent/'fixtures'/'synthetic_sardinia.zip',
                source_id='demo', archive_dir=tmp_path/'raw')
    return con, active_snapshots(con)[0]


def test_round_trip_reproduces_geometry_and_levels(tmp_path):
    con, snapshot = _loaded(tmp_path)
    folder = tmp_path/'shapes'
    try:
        first = {}
        before = shape_store.hydrate(first, folder, snapshot, limit=SHAPE_CACHE_LIMIT)
        assert before == {}
        expected = [network_geometry(con, date(2026, 11, 6), max_points=120000, shape_cache=first)]
        assert shape_store.persist(first, folder, snapshot, before)
        middle = shape_store.inventory(first, snapshot['snapshot_id'])
        assert not shape_store.persist(first, folder, snapshot, middle)
        # A later build that only adds coarser levels writes a second, small delta.
        expected.append(network_geometry(con, date(2026, 11, 6), max_points=4, shape_cache=first))
        assert shape_store.persist(first, folder, snapshot, middle)
        assert len(list(folder.glob('*--*.json'))) == 2

        restarted = {}
        assert shape_store.hydrate(restarted, folder, snapshot, limit=SHAPE_CACHE_LIMIT) == shape_store.inventory(first, snapshot['snapshot_id'])
        assert restarted == first
        assert [network_geometry(con, date(2026, 11, 6), max_points=p, shape_cache=restarted) for p in (120000, 4)] == expected
    finally:
        con.close()


def test_mismatched_or_corrupt_files_are_ignored(tmp_path):
    con, snapshot = _loaded(tmp_path)
    folder = tmp_path/'shapes'
    try:
        cache = {}
        network_geometry(con, date(2026, 11, 6), shape_cache=cache)
        shape_store.persist(cache, folder, snapshot, {})
        path = next(folder.glob('*--*.json'))
        data = json.loads(path.read_text(encoding='utf-8'))

        for change in ({'content_sha256': 'other'}, {'algorithm': 'older'}, {'format': 'v0'}):
            path.write_text(json.dumps({**data, **change}), encoding='utf-8')
            assert shape_store.hydrate({}, folder, snapshot, limit=SHAPE_CACHE_LIMIT) == {}
        path.write_text('{not json', encoding='utf-8')
        assert shape_store.hydrate({}, folder, snapshot, limit=SHAPE_CACHE_LIMIT) == {}
        assert shape_store.hydrate({}, tmp_path/'missing', snapshot, limit=SHAPE_CACHE_LIMIT) == {}
    finally:
        con.close()


def test_deltas_compact_into_main_file(tmp_path, monkeypatch):
    con, snapshot = _loaded(tmp_path)
    folder = tmp_path/'shapes'
    monkeypatch.setattr(shape_store, 'COMPACT_AFTER_DELTAS', 2)
    try:
        cache = {}
        network_geometry(con, date(2026, 11, 6), shape_cache=cache)
        shape_store.persist(cache, folder, snapshot, {})
        middle = shape_store.inventory(cache, snapshot['snapshot_id'])
        network_geometry(con, date(2026, 11, 6), max_points=4, shape_cache=cache)
        shape_store.persist(cache, folder, snapshot, middle)
        restarted = {}
        shape_store.hydrate(restarted, folder, snapshot, limit=SHAPE_CACHE_LIMIT)
        assert restarted == cache
        assert not list(folder.glob('*--*.json'))
        assert (folder/f"{snapshot['snapshot_id']}.json").exists()
        again = {}
        shape_store.hydrate(again, folder, snapshot, limit=SHAPE_CACHE_LIMIT)
        assert again == cache
    finally:
        con.close()
