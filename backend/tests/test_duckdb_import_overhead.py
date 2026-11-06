"""DuckDB's Python client probes `import pandas` on each execute. When pandas is
absent, every failed probe rescans sys.path under the global import lock, which
made each query cost milliseconds and serialized concurrent API requests."""
import importlib.util
import sys
import pytest
from transport_atlas.storage import connect, rows


class _Recorder:
    def __init__(self): self.names = []
    def find_spec(self, name, path=None, target=None):
        self.names.append(name)
        return None


def test_queries_do_not_repeat_failed_optional_imports(tmp_path):
    pytest.importorskip('duckdb')
    if importlib.util.find_spec('pandas') is not None:
        pytest.skip('pandas is installed; DuckDB probes resolve from sys.modules')
    con = connect(tmp_path / 'probe.duckdb', backend='duckdb')
    recorder = _Recorder()
    sys.meta_path.insert(0, recorder)
    try:
        for _ in range(20):
            rows(con, 'SELECT ?::INTEGER AS value', (1,))
    finally:
        sys.meta_path.remove(recorder)
        con.close()
    assert 'pandas' not in recorder.names
