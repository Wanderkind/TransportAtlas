"""Build an isolated DuckDB holding two synthetic versions of one fictional operator.

Writes only under ignored data/history-demo/ and refuses any other target, so the
real four-source database and archives are never opened or changed. Serve it with:

    .\\scripts\\start_local.ps1 -Production -Database data/history-demo/history_demo.duckdb

The feeds are invented (agency "SYNTHETIC Valley Transit"); never present results
as observed history of a real network.

--admin-stage exercises the Phase 7A administration path instead of the legacy
importer for v2 (services must be stopped; backups go to data/history-demo/backups):

  legacy     v1, v2, other via import_file(activate=True)  -> v2 current (default)
  preserved  v1, other bootstrapped; v2 via manage_snapshots import  -> v1 stays current
  activated  as preserved, then activate v2 with --expected-current v1  -> v2 current
  archived   Phase 7C: v2 and other bootstrapped (v2 with an invented publisher-direct
             record); then the OLDER v1 imported with --archive-provenance from the
             invented "SYNTHETIC Example Archive" (observed 2026-08-28, obtained now)
             -> v2 stays current. Mirrors the shape of the ASPO case without any real data.
  browser-archived  Phase 7D1: as archived, but v1's record is written by
             `manage_snapshots.py archive-record` logic for a browser download from the
             invented "SYNTHETIC Example Browser Archive" (delivery unknown, observed on
             2026-08-28, date only).
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
    parser.add_argument('--admin-stage', choices=['legacy', 'preserved', 'activated', 'archived', 'browser-archived'], default='legacy')
    args = parser.parse_args(argv)
    folder = args.folder.resolve()
    if folder != DEMO.resolve():
        parser.error('The demo database may only be built in data/history-demo/.')
    if folder.exists():
        shutil.rmtree(folder)  # only the disposable demo folder
    import history_fixtures as hf
    from transport_atlas.storage import connect
    db = folder/'history_demo.duckdb'
    con = connect(db, 'duckdb')
    try:
        if args.admin_stage == 'legacy':
            ids = hf.import_all(con, folder/'zips', folder/'raw')
        elif args.admin_stage in ('archived', 'browser-archived'):
            import json
            from datetime import datetime, timezone
            from transport_atlas.admin import sha256_file
            from transport_atlas.ingest import import_file
            paths = hf.build(folder/'zips')
            now = datetime.now(timezone.utc).isoformat()
            Path(str(paths['v2']) + '.provenance.json').write_text(json.dumps({
                'source_id': hf.SOURCE, 'sha256': sha256_file(paths['v2']), 'retrieved_at': now,
                'download_url': 'https://publisher.example.test/synthetic_valley/gtfs.zip',
                'landing_url': 'https://publisher.example.test/synthetic_valley/'}, indent=2), encoding='utf-8')
            ids = {key: import_file(con, paths[key], source_id=source, archive_dir=folder/'raw',
                                    publisher='SYNTHETIC fixture (not a real operator)')['snapshot_id']
                   for key, source in (('v2', hf.SOURCE), ('other', hf.OTHER_SOURCE))}
        else:
            from transport_atlas.ingest import import_file
            paths = hf.build(folder/'zips')
            ids = {key: import_file(con, paths[key], source_id=source, archive_dir=folder/'raw',
                                    publisher='SYNTHETIC fixture (not a real operator)')['snapshot_id']
                   for key, source in (('v1', hf.SOURCE), ('other', hf.OTHER_SOURCE))}
    finally:
        con.close()
    if args.admin_stage in ('archived', 'browser-archived'):
        import archive_fixtures as af
        from datetime import datetime, timezone
        from transport_atlas import admin
        target = admin.resolve_database(db)
        incoming = folder/'incoming'
        incoming.mkdir()
        older = incoming/'synthetic_valley_v1.zip'
        shutil.copyfile(folder/'zips'/'synthetic_valley_v1.zip', older)
        publisher = 'SYNTHETIC fixture (not a real operator)'
        demo_catalog = af.catalog(hf.SOURCE, publisher)
        if args.admin_stage == 'browser-archived':
            providers = af.BROWSER_PROVIDERS
            from transport_atlas.archive_provenance import sha1_file
            admin.write_archive_record(older, hf.SOURCE, provider=af.BROWSER_PROVIDER, feed_id=af.FEED_ID,
                                       expected_sha1=sha1_file(older),
                                       observed_on=af.OBSERVED_ON, catalog_data=demo_catalog, providers=providers)
        else:
            providers = af.PROVIDERS
            af.write(older, af.record(older, hf.SOURCE, publisher, sha256=admin.sha256_file(older),
                                      retrieved_at=datetime.now(timezone.utc).isoformat(),
                                      observed_at='2026-08-28T06:00:00+00:00'))
        out = admin.apply_import(target, older, hf.SOURCE, archive=True, providers=providers,
                                 catalog_data=demo_catalog, backup_root=folder/'backups')
        ids['v1'] = out['result']['snapshot_id']
        print(f"  admin archive import v1 (selection preserved): backup {out['backup']['backup_file']}")
    elif args.admin_stage != 'legacy':
        from transport_atlas import admin
        target = admin.resolve_database(db)
        out = admin.apply_import(target, folder/'zips'/'synthetic_valley_v2.zip', hf.SOURCE, synthetic=True,
                                 backup_root=folder/'backups')
        ids['v2'] = out['result']['snapshot_id']
        print(f"  admin import v2 (selection preserved): backup {out['backup']['backup_file']}")
        if args.admin_stage == 'activated':
            out = admin.apply_selection(target, hf.SOURCE, ids['v2'], ids['v1'], backup_root=folder/'backups')
            print(f"  admin activate v2 (expected current v1): backup {out['backup']['backup_file']}")
        ro = admin.open_readonly(target)
        try:
            print(f"  selection: {admin.logical_state(ro)['selection']}")
        finally:
            ro.close()
    print(f"Synthetic history demo ({args.admin_stage}): {db}")
    for key, value in ids.items():
        print(f'  {key}: {value}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
