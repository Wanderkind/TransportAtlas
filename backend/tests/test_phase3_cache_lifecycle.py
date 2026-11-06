"""Disposable network-response files are bounded by least-recent use."""
import os
from datetime import date
from transport_atlas import network_cache


def _write(folder, n, mtime, size=10):
    key = format(n, '064x')
    path = folder/(key+'.json')
    path.write_bytes(b'x'*size)
    os.utime(path, (mtime, mtime))
    return key


def test_prune_removes_least_recently_used_beyond_file_and_byte_bounds(tmp_path):
    keys = [_write(tmp_path, n, 1000+n) for n in range(6)]
    (tmp_path/'notes.txt').write_text('unrelated')
    removed = network_cache.prune(tmp_path, max_files=4, max_bytes=10**9, now=5000)
    assert sorted(removed) == sorted(k+'.json' for k in keys[:2])
    removed = network_cache.prune(tmp_path, max_files=100, max_bytes=25, keep=keys[2], now=5000)
    assert keys[2]+'.json' not in removed and len(removed) == 2
    assert (tmp_path/'notes.txt').exists() and (tmp_path/(keys[2]+'.json')).exists()


def test_stale_partial_files_are_cleaned_and_reads_refresh_recency(tmp_path):
    old = tmp_path/'a.123.part'; old.write_bytes(b'x'); os.utime(old, (0, 0))
    fresh = tmp_path/'b.456.part'; fresh.write_bytes(b'x')
    network_cache.prune(tmp_path, max_files=10, max_bytes=10**9)
    assert not old.exists() and fresh.exists()
    key = network_cache.cache_key(('s',), date(2026, 11, 6), 'all', None, None, True, 1200, 120000)
    network_cache.write_cache(tmp_path, key, {'type': 'FeatureCollection', 'features': [], 'metadata': {}})
    path = tmp_path/(key+'.json'); os.utime(path, (1, 1))
    assert network_cache.read_cache(tmp_path, key) is not None
    assert path.stat().st_mtime > 1
