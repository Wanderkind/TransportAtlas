"""Build an isolated DuckDB holding two synthetic versions of one fictional operator.

Writes only under ignored data/history-demo/ and refuses any other target, so the
real four-source database and archives are never opened or changed. Serve it with:

    .\\scripts\\start_local.ps1 -Production -Database data/history-demo/history_demo.duckdb

The feeds are invented (agency "SYNTHETIC Valley Transit"); never present results
as observed history of a real network.
"""
from __future__ import annotations
import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT/'data'/'history-demo'
sys.path[:0] = [str(ROOT/'backend'), str(ROOT/'backend'/'tests')]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--folder', type=Path, default=DEMO)
    args = parser.parse_args(argv)
    folder = args.folder.resolve()
    if folder != DEMO.resolve():
        parser.error('The demo database may only be built in data/history-demo/.')
    if folder.exists():
        shutil.rmtree(folder)  # only the disposable demo folder
    import history_fixtures as hf
    from transport_atlas.storage import connect
    con = connect(folder/'history_demo.duckdb', 'duckdb')
    try:
        ids = hf.import_all(con, folder/'zips', folder/'raw')
    finally:
        con.close()
    print(f"Synthetic history demo: {folder/'history_demo.duckdb'}")
    for key, value in ids.items():
        print(f'  {key}: {value}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
