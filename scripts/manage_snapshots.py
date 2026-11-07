"""Guarded snapshot administration for a TransportAtlas database (Phase 7A, 7C).

Every command is a read-only preview unless --apply is given. Mutations require the
local services to be stopped, take and verify a backup first, and never change the
atlas selection except through `activate` / `restore` with an expected-current check.

  status          list sources, snapshots, selection, archives (read-only)
  import          import an existing local ZIP WITHOUT selecting it
                  (--archive-provenance: a copy obtained from a third-party archive,
                  attributed to that archive; see docs/PHASE7C_ARCHIVE_PROVENANCE.md)
  archive-record  write the provenance record for a ZIP the user downloaded from an
                  archive in a browser (Phase 7D1; no database involved)
  activate        select a ready snapshot (or `none`) given --expected-current
  restore         undo the most recent journaled selection change of a source
  verify-backup   re-hash a backup and compare it with its record (read-only)
  restore-backup  copy a verified backup to a NEW path (never overwrites)

Examples (preview first; add --apply only after reading the plan):

  python scripts/manage_snapshots.py status --db data/transport_atlas.duckdb
  python scripts/manage_snapshots.py import --db data/history-demo/history_demo.duckdb \\
      --source synthetic_valley --zip data/history-demo/zips/synthetic_valley_v2.zip --synthetic
  python scripts/manage_snapshots.py activate --db ... --source S --snapshot ID --expected-current ID|none

Mutating the real atlas database (data/transport_atlas.duckdb) additionally needs
--real-database. Phase 7A did not authorize doing so; see
docs/PHASE7A_SNAPSHOT_ADMINISTRATION.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from transport_atlas import admin  # noqa: E402


def _print(obj, as_json=True):
    # Plans and results are structured; JSON is the faithful rendering in both modes.
    print(json.dumps(obj, indent=2, default=str))


def _banner(target):
    d = target.describe()
    print(f"Database: {d['path']}  [{d['backend']}, {d['bytes']} bytes, modified {d['modified']}]")
    if d['real_atlas_database']:
        print('*** TARGET IS THE REAL ATLAS DATABASE ***')


def _status_text(report):
    for src in report['sources']:
        print(f"\n{src['source_id']}  ({src['publisher']})  selected: {src['selected_snapshot_id'] or 'none'}")
        for s in src['snapshots']:
            mark = '*' if s['selected'] else ' '
            arch = 'archive ok' if s['archive']['present'] else 'ARCHIVE MISSING'
            if s['archive']['sha256_verified'] is False:
                arch = 'ARCHIVE HASH MISMATCH'
            elif s['archive']['sha256_verified']:
                arch += ' (hash verified)'
            print(f"  {mark} {s['snapshot_id']}  sha256 {s['short_sha256']}  {s['import_status']:<7} "
                  f"coverage {s['coverage']['start']}..{s['coverage']['end']}  "
                  f"version {s['feed_version'] or '-'}  retrieved {s['retrieved_at']}  {arch}  "
                  f"provenance {s.get('provenance_kind', '-')}"
                  f"{'' if s['archive']['provenance_sidecar'] else '  (no provenance sidecar)'}")
            print(f"      full sha256 {s['content_sha256']}")
    print(f"\nArchive roots: {', '.join(report['archive_dirs']) or 'none'}")
    for key, label in (('unreferenced_archives', 'Unreferenced archives'), ('partial_files', 'Partial files')):
        print(f"{label}: {len(report[key])}")
        for f in report[key]:
            print(f'  {f}')


def _confirm_real(target, args):
    if target.is_real and args.apply and not args.real_database:
        raise admin.AdminError('Refusing to mutate the real atlas database without --real-database '
                               '(and fresh user authorization; Phase 7A does not grant it).')


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='command', required=True)

    def common(sp, mutating=True):
        sp.add_argument('--db', required=True, type=Path, help='Existing database file (never created)')
        sp.add_argument('--backend', choices=['duckdb', 'sqlite'], help='Override suffix-based detection')
        sp.add_argument('--json', action='store_true', help='Machine-readable output')
        if mutating:
            sp.add_argument('--apply', action='store_true', help='Perform the change (default: preview only)')
            sp.add_argument('--backup-dir', type=Path, default=admin.DEFAULT_BACKUPS,
                            help='Where verified backups and the selection journal are written')
            sp.add_argument('--real-database', action='store_true',
                            help='Required to mutate data/transport_atlas.duckdb')

    sp = sub.add_parser('status', help='Read-only inventory')
    common(sp, mutating=False)
    sp.add_argument('--source')
    sp.add_argument('--verify-archives', action='store_true', help='Re-hash each archived ZIP')

    sp = sub.add_parser('import', help='Import an existing local ZIP without selecting it')
    common(sp)
    sp.add_argument('--source', required=True)
    sp.add_argument('--zip', required=True, type=Path)
    sp.add_argument('--archive-dir', help='Archive root (default: inferred from existing snapshots)')
    sp.add_argument('--new-source', action='store_true', help='Allow a source not yet in this database')
    sp.add_argument('--synthetic', action='store_true',
                    help='Invented test fixture without provenance (refused for official catalog sources)')
    sp.add_argument('--archive-provenance', action='store_true',
                    help='The ZIP came from a third-party archive (e.g. Transitland): require and validate a '
                         'third-party archive provenance record (configs/archive_providers.json). Never '
                         'updates the source official download URL; never activates.')

    sp = sub.add_parser('archive-record', help='Preview/write the archive record of a browser-downloaded ZIP')
    sp.add_argument('--zip', required=True, type=Path, help='The downloaded ZIP (never inside data/raw)')
    sp.add_argument('--source', required=True, help='Catalog source id, e.g. aspo')
    sp.add_argument('--provider', required=True, help='Provider key in configs/archive_providers.json')
    sp.add_argument('--feed-id', required=True, help="The provider's feed id, e.g. f-spqb-aspoolbia")
    sp.add_argument('--acquisition', required=True, choices=['authenticated_browser'],
                    help='How the copy was obtained (only browser downloads are supported here)')
    sp.add_argument('--expected-sha1', required=True,
                    help='The archive version id (40-hex SHA-1) as shown on the provider\'s public version page, '
                         'supplied independently; must equal the SHA-1 of the ZIP')
    sp.add_argument('--expected-record-url',
                    help='Optional: the exact public version page URL; must be the canonical page for --expected-sha1')
    sp.add_argument('--observed-on', help='Date the archive fetched it from the publisher (YYYY-MM-DD), '
                                          'as shown on the public version page')
    sp.add_argument('--retrieved-at', help='When you obtained the copy (ISO time with timezone); '
                                           'default: the file modification time')
    sp.add_argument('--write', action='store_true', help='Create the record next to the ZIP (never overwrites)')
    sp.add_argument('--json', action='store_true')

    sp = sub.add_parser('activate', help='Select a ready snapshot for the atlas')
    common(sp)
    sp.add_argument('--source', required=True)
    sp.add_argument('--snapshot', required=True, help=f'Full snapshot ID, or "{admin.NONE}" to deselect')
    sp.add_argument('--expected-current', required=True, help=f'Current snapshot ID, or "{admin.NONE}"')

    sp = sub.add_parser('restore', help='Undo the most recent journaled selection change')
    common(sp)
    sp.add_argument('--source', required=True)

    sp = sub.add_parser('verify-backup', help='Re-hash and check a backup (read-only)')
    sp.add_argument('--metadata', required=True, type=Path, help='backup.json of the backup')
    sp.add_argument('--json', action='store_true')

    sp = sub.add_parser('restore-backup', help='Copy a verified backup to a NEW path')
    sp.add_argument('--metadata', required=True, type=Path)
    sp.add_argument('--to', required=True, type=Path)
    sp.add_argument('--apply', action='store_true')
    sp.add_argument('--json', action='store_true')

    args = p.parse_args(argv)
    try:
        if args.command == 'verify-backup':
            _print(admin.verify_backup(args.metadata), args.json)
            return 0
        if args.command == 'archive-record':
            kw = dict(provider=args.provider, feed_id=args.feed_id, observed_on=args.observed_on,
                      retrieved_at=args.retrieved_at, expected_sha1=args.expected_sha1,
                      expected_record_url=args.expected_record_url)
            if args.write:
                result = admin.write_archive_record(args.zip, args.source, **kw)
                _print(result, args.json)
                print(f"Written: {result['sidecar']} (no database was opened)")
                return 0
            result = admin.plan_archive_record(args.zip, args.source, **kw)
            _print(result, args.json)
            if result['problems']:
                print('PREVIEW: the record would be REFUSED: ' + '; '.join(result['problems']))
                return 2
            print('Preview only: nothing was written. Re-run with --write to create the record.')
            return 0
        if args.command == 'restore-backup':
            result = admin.restore_backup(args.metadata, args.to, apply=args.apply)
            _print(result, args.json)
            if not args.apply:
                print('Preview only: re-run with --apply to write the copy.')
            return 0
        target = admin.resolve_database(args.db, args.backend)
        if not args.json:
            _banner(target)
        if args.command == 'status':
            report = admin.status(target, source_id=args.source, verify_archives=args.verify_archives)
            if args.json:
                _print(report)
            else:
                _status_text(report)
            return 0
        _confirm_real(target, args)
        if args.command == 'import':
            kw = dict(archive_dir=args.archive_dir, new_source=args.new_source, synthetic=args.synthetic,
                      archive=args.archive_provenance)
            if args.apply:
                result = admin.apply_import(target, args.zip, args.source, backup_root=args.backup_dir, **kw)
            else:
                result = admin.plan_import(target, args.zip, args.source, **kw)
        elif args.command == 'activate':
            if args.apply:
                result = admin.apply_selection(target, args.source, args.snapshot, args.expected_current,
                                               backup_root=args.backup_dir)
            else:
                result = admin.plan_selection(target, args.source, args.snapshot, args.expected_current)
        else:  # restore
            if args.apply:
                result = admin.apply_restore(target, args.source, backup_root=args.backup_dir)
            else:
                result = admin.plan_restore(target, args.source, args.backup_dir)
        _print(result, args.json)
        if not args.apply:
            if result.get('problems'):
                print('PREVIEW: the change would be REFUSED: ' + '; '.join(result['problems']))
                return 2
            print('Preview only: nothing was changed. Re-run with --apply (services stopped) to perform it.')
        return 0
    except admin.AdminError as exc:
        print(f'REFUSED: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
