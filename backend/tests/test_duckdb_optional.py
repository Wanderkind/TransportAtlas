"""Integration smoke test for the target backend (skips only when DuckDB unavailable)."""
import pytest
from pathlib import Path
from transport_atlas.ingest import import_file
from transport_atlas.schedule import daily_summary,stop_board
from transport_atlas.storage import connect


def test_real_duckdb_backend(tmp_path):
    pytest.importorskip('duckdb')
    c=connect(tmp_path/'test.duckdb',backend='duckdb')
    try:
        result=import_file(c,Path(__file__).parent/'fixtures'/'synthetic_sardinia.zip',
                           source_id='synthetic_demo',archive_dir=tmp_path/'raw')
        s=result['snapshot_id']
        assert daily_summary(c,'2026-11-06')['snapshots'][0]['scheduled_trip_instances']==4
        assert any(x['gtfs_time']=='25:15:00' for x in stop_board(c,s,'B','2026-11-07')['departures'])
    finally:
        c.close()
