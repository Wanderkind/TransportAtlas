"""Phase 7D2: build a hash-verified, non-overwriting local recovery bundle (read-only on originals).

Writes only under a NEW folder data/backups/phase7d2-post-import-evidence-<UTC>/ (git-ignored):

  database/             consistent post-import copy via admin.create_backup (writer-excluding lock)
  pre-import-database/  byte copy of the verified Phase 7D1 pre-import backup
  archives/<source>/    every ZIP referenced by feed_snapshots, plus its provenance sidecar
  downloads/            the user-downloaded historical ASPO ZIP and its record
  evidence-phase7d0/    Phase 7D0 inventories, checksum lists and offline summaries
  manifest.json, SHA256SUMS, SHA1SUMS, README-RESTORE.md

Never writes to the real database, data/raw or data/downloads. Usage:
    python scripts/build_evidence_bundle.py [--restore-test]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from transport_atlas import admin  # noqa: E402

DB = ROOT / 'data' / 'transport_atlas.duckdb'
PRE_IMPORT = ROOT / 'data' / 'backups' / 'transport_atlas-20261107T122036599403Z-64c8d142'
DOWNLOAD = ROOT / 'data' / 'downloads' / 'archive' / 'transitland' / 'aspo' / \
    'f-spqb-aspoolbia-b48388a26eeffbadde634b526dc3cb1acd8606ab.zip'
P7D0 = ROOT / 'data' / 'local-verification' / 'phase7d0'
P7D0_FILES = ['evidence-inventory.json', 'SHA256SUMS', 'SHA1SUMS', 'gtfs-historical-b48388a2.json',
              'gtfs-current-a710bc7c.json', 'isolated-compare/summary.json', 'isolated-compare/inventory.json',
              'isolated-compare/comparison-same-date-2026-11-06.json',
              'validator-historical-b48388a2/report.json', 'validator-historical-b48388a2/system_errors.json',
              'validator-current-a710bc7c/report.json', 'validator-current-a710bc7c/system_errors.json']


def digest(path: Path, algo='sha256') -> str:
    h = hashlib.new(algo)
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--restore-test', action='store_true',
                    help='Also restore the bundled post-import copy to a disposable path and compare it')
    args = ap.parse_args(argv)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    bundle = ROOT / 'data' / 'backups' / f'phase7d2-post-import-evidence-{stamp}'
    bundle.mkdir(parents=True, exist_ok=False)  # never reuse or overwrite
    target = admin.resolve_database(DB)
    db_before = digest(DB)
    con = admin.open_readonly(target)
    try:
        state_before = admin.logical_state(con)
        snaps = admin._rows(con, 'SELECT snapshot_id, source_id, content_sha256, raw_zip_path FROM feed_snapshots '
                                 'ORDER BY source_id, snapshot_id')
        selection = admin._rows(con, 'SELECT source_id, snapshot_id FROM selection_members ORDER BY source_id')
        sources = admin._rows(con, 'SELECT source_id, download_url FROM feed_sources ORDER BY source_id')
    finally:
        con.close()
    entries = []

    def add(src: Path, rel: str, role: str, **extra):
        dst = bundle / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            raise SystemExit(f'refusing to overwrite {dst}')
        original = digest(src)
        shutil.copy2(src, dst)
        copied = digest(dst)
        if copied != original or digest(src) != original:
            raise SystemExit(f'copy mismatch for {src}')
        entries.append({'path': rel, 'role': role, 'original_path': str(src.relative_to(ROOT)).replace('\\', '/'),
                        'bytes': dst.stat().st_size, 'sha256': copied, **extra})

    # 1. consistent post-import database copy (the project's tested backup mechanism)
    meta = admin.create_backup(target, bundle / 'database', label='phase7d2 post-import evidence bundle')
    db_copy = Path(meta['backup_file'])
    entries.append({'path': str(db_copy.relative_to(bundle)).replace('\\', '/'), 'role': 'post_import_database',
                    'original_path': 'data/transport_atlas.duckdb', 'bytes': meta['bytes'], 'sha256': meta['sha256']})
    meta_file = Path(meta['metadata_file'])
    entries.append({'path': str(meta_file.relative_to(bundle)).replace('\\', '/'), 'role': 'post_import_backup_metadata',
                    'bytes': meta_file.stat().st_size, 'sha256': digest(meta_file)})
    # 2. the verified pre-import backup (byte copy; it is already a consistent cold copy)
    for f in sorted(PRE_IMPORT.iterdir()):
        add(f, f'pre-import-database/{f.name}', 'pre_import_backup')
    # 3. every referenced archive and its sidecar
    for s in snaps:
        zip_path = admin.resolve_archive(s['raw_zip_path'])
        if digest(zip_path) != s['content_sha256']:
            raise SystemExit(f'archive hash mismatch for {zip_path}')
        rel = f"archives/{s['source_id']}/{zip_path.name}"
        add(zip_path, rel, 'snapshot_archive', source_id=s['source_id'], snapshot_id=s['snapshot_id'])
        side = zip_path.with_suffix('.zip.provenance.json')
        if side.is_file():
            add(side, rel + '.provenance.json', 'snapshot_provenance', source_id=s['source_id'],
                snapshot_id=s['snapshot_id'])
    # 4. the user download and its record
    add(DOWNLOAD, f'downloads/{DOWNLOAD.name}', 'user_download_historical_aspo', sha1=digest(DOWNLOAD, 'sha1'))
    side = Path(str(DOWNLOAD) + '.provenance.json')
    if side.is_file():
        add(side, f'downloads/{side.name}', 'user_download_record')
    # 5. Phase 7D0 evidence
    for name in P7D0_FILES:
        if (P7D0 / name).is_file():
            add(P7D0 / name, f'evidence-phase7d0/{name}', 'phase7d0_evidence')

    # Originals unchanged?
    con = admin.open_readonly(target)
    try:
        state_after = admin.logical_state(con)
        selection_after = admin._rows(con, 'SELECT source_id, snapshot_id FROM selection_members ORDER BY source_id')
    finally:
        con.close()
    checks = {'real_db_sha256_before': db_before, 'real_db_sha256_after': digest(DB),
              'logical_state_unchanged': state_before == state_after, 'selection_unchanged': selection == selection_after}
    if not (checks['logical_state_unchanged'] and checks['selection_unchanged'] and db_before == checks['real_db_sha256_after']):
        raise SystemExit(f'original changed during bundling: {checks}')

    restore = None
    if args.restore_test:
        dest = ROOT / 'data' / 'local-verification' / 'phase7d2' / f'restore-test-{stamp}' / 'transport_atlas.duckdb'
        out = admin.restore_backup(meta_file, dest, apply=True)
        rt = admin.resolve_database(dest)
        con = admin.open_readonly(rt)
        try:
            restored_state = admin.logical_state(con)
        finally:
            con.close()
        restore = {'destination': str(dest.relative_to(ROOT)).replace('\\', '/'), 'sha256': digest(dest),
                   'matches_bundle': digest(dest) == meta['sha256'], 'logical_state_matches': restored_state == state_before,
                   'result': {k: out[k] for k in ('applied', 'sha256')}}

    hist = next(e for e in entries if e['role'] == 'user_download_historical_aspo')
    manifest = {'kind': 'transport-atlas-evidence-bundle', 'phase': '7D2', 'created_at_utc': stamp,
                'real_database': {'path': 'data/transport_atlas.duckdb', 'sha256': db_before, 'bytes': DB.stat().st_size,
                                  'selection': selection, 'feed_sources_download_url': sources,
                                  'snapshots': [{k: s[k] for k in ('source_id', 'snapshot_id', 'content_sha256')} for s in snaps]},
                'historical_aspo': {'sha1': hist['sha1'], 'sha256': hist['sha256'], 'transitland_version': hist['sha1'],
                                    'snapshot_id': '396fbbacf74f0ad576e6d7919ec3092b'},
                'checks': checks, 'restore_test': restore, 'files': entries}
    (bundle / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8', newline='\n')
    (bundle / 'SHA256SUMS').write_text(''.join(f"{e['sha256']}  {e['path']}\n" for e in entries), encoding='utf-8',
                                       newline='\n')
    (bundle / 'SHA1SUMS').write_text(f"{hist['sha1']}  {hist['path']}\n", encoding='utf-8', newline='\n')
    (bundle / 'README-RESTORE.md').write_text(README.format(bundle=bundle.name, db=db_before, pre=PRE_IMPORT.name),
                                              encoding='utf-8', newline='\n')
    # Final self-check of every copied file against the manifest.
    bad = [e['path'] for e in entries if digest(bundle / e['path']) != e['sha256']]
    if bad:
        raise SystemExit(f'manifest check failed: {bad}')
    print(json.dumps({'bundle': str(bundle.relative_to(ROOT)), 'files': len(entries),
                      'bytes': sum(e['bytes'] for e in entries), 'checks': checks, 'restore_test': restore}, indent=2))
    return 0


README = """# TransportAtlas evidence bundle `{bundle}`

Made by `scripts/build_evidence_bundle.py` (Phase 7D2). Everything here is a byte copy; the
originals were only read. **This folder is not an off-disk copy** until you copy it to another drive.

| folder | content |
|---|---|
| `database/` | **post-import** real database (after the historical ASPO import), consistent copy made by `manage_snapshots`'s backup routine; source SHA-256 `{db}`. |
| `pre-import-database/` | the verified **pre-import** backup `{pre}` (SHA-256 `3c308708…3ca0`), state before the historical ASPO import. |
| `archives/<source>/` | every archive ZIP referenced by the database's `feed_snapshots`, with its provenance record. |
| `downloads/` | the ZIP you downloaded from Transitland (SHA-1 `b48388a26eeffbadde634b526dc3cb1acd8606ab`) and its record. |
| `evidence-phase7d0/` | Phase 7D0 inventories, checksum lists, GTFS/validator/comparison summaries. |

## Verify (Git Bash, inside this folder)

    sha256sum -c SHA256SUMS && sha1sum -c SHA1SUMS

## Verify (PowerShell, inside this folder)

    Get-Content SHA256SUMS | ForEach-Object {{ $h,$p = $_ -split '  ',2; if ((Get-FileHash -Algorithm SHA256 $p).Hash.ToLower() -ne $h) {{ "MISMATCH $p" }} }}

No output means every file matches.

## Restore (never into the live path while services run)

The `backup.json` files record absolute paths from when they were made, so after moving this
folder use the hash-checked manual route:

1. Stop services: `./scripts/stop_local.ps1`.
2. Copy the wanted database to a NEW path, e.g. `data/restored/transport_atlas.duckdb`:
   `database/<folder>/transport_atlas.duckdb` = state **after** the historical ASPO import;
   `pre-import-database/transport_atlas.duckdb` = state **before** it.
3. Check its SHA-256 against `SHA256SUMS`, then inspect it read-only:
   `python scripts/manage_snapshots.py status --db data/restored/transport_atlas.duckdb --verify-archives`.
4. Copy `archives/<source>/*` back to `data/raw/<source>/` only for files that are missing (never over a
   different file), and re-run step 3 until every archive verifies.
5. Only then, with services stopped, swap the live `data/transport_atlas.duckdb` by hand, keeping the old file.

While the original paths still exist, `manage_snapshots.py restore-backup --metadata <backup.json> --to <new path> --apply`
does steps 2–3 for you.
"""

if __name__ == '__main__':
    raise SystemExit(main())
