"""Phase 7A: guarded, auditable snapshot administration (CLI only).

Nothing in the web application imports this module; the atlas and History GET
endpoints stay read-only. Entry point: ``scripts/manage_snapshots.py``.

Invariants enforced here (see docs/PHASE7A_SNAPSHOT_ADMINISTRATION.md):

* An existing database is required; a misspelled path never creates a new one.
* Inspection opens the database read-only. Every mutation first makes and verifies
  a consistent backup while holding a lock that excludes writers, then reopens for
  writing and aborts if the logical state moved since the backup.
* Administrative imports never change ``selection_members`` (``activate=False``).
* A selection change targets only a ready snapshot of the same source whose archive
  is present and hash-verified, and only if the current selection equals the
  caller's ``expected_current`` (checked inside the write transaction).
* Each selection change is journaled (intent, then outcome) for ``restore``.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from zipfile import BadZipFile, ZipFile

from .gtfs import FeedError, read_feed
from .ingest import _validate, import_file
from .official import ALLOWED_OFFICIAL_HOSTS, catalog, safe_url
from .provenance import declared_info

ROOT = Path(__file__).resolve().parents[2]
REAL_DB = ROOT / 'data' / 'transport_atlas.duckdb'
DEFAULT_BACKUPS = ROOT / 'data' / 'backups'
SERVICES_RECORD = ROOT / 'data' / 'local-services.json'
NONE = 'none'  # explicit "no selection" token on the command line
SYNTHETIC_PUBLISHER = 'SYNTHETIC fixture (not a real operator)'
SOURCE_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}')
SNAPSHOT_RE = re.compile(r'[0-9a-f]{32}')
JOURNAL = 'selection-journal.jsonl'
DEFAULT = object()  # resolved at call time so tests can redirect module paths


class AdminError(RuntimeError):
    """A refused or failed administrative operation. Nothing was changed unless the
    message says otherwise."""


# -- target database ------------------------------------------------------------------

class Target:
    def __init__(self, path: Path, backend: str):
        self.path, self.backend = path, backend

    @property
    def is_real(self) -> bool:
        return self.path == REAL_DB.resolve()

    def describe(self) -> dict:
        stat = self.path.stat()
        return {'path': str(self.path), 'backend': self.backend, 'bytes': stat.st_size,
                'modified': datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
                'real_atlas_database': self.is_real}


def resolve_database(path, backend: str | None = None) -> Target:
    p = Path(path).expanduser().resolve()
    if not p.is_file():
        raise AdminError(f'Database not found: {p}. Refusing to create a new database; check the path.')
    if backend is None:
        suffix = p.suffix.lower()
        backend = 'duckdb' if suffix == '.duckdb' else 'sqlite' if suffix in ('.sqlite', '.sqlite3', '.db') else None
        if backend is None:
            raise AdminError(f'Cannot infer the database backend from {p.name}; pass --backend duckdb|sqlite.')
    if backend not in ('duckdb', 'sqlite'):
        raise AdminError(f'Unsupported backend {backend!r}')
    return Target(p, backend)


def _lock_error(target: Target, exc: Exception) -> AdminError:
    return AdminError(f'Cannot open {target.path} ({type(exc).__name__}: {str(exc).splitlines()[0][:200]}). '
                      'It is probably locked by another process, such as the running backend; '
                      'stop services with scripts/stop_local.ps1 and retry. Nothing was changed.')


def _check_schema(con, target: Target):
    for table in ('feed_sources', 'feed_snapshots', 'selection_members'):
        try:
            _rows(con, f'SELECT * FROM {table} LIMIT 0')
        except Exception as exc:
            text = str(exc).lower()
            if 'no such table' in text or 'does not exist' in text:
                raise AdminError(f'{target.path} is not a TransportAtlas database (missing {table}).') from exc
            raise _lock_error(target, exc) from exc


def open_readonly(target: Target):
    try:
        if target.backend == 'duckdb':
            import duckdb
            con = duckdb.connect(str(target.path), read_only=True)
        else:
            con = sqlite3.connect(f'{target.path.as_uri()}?mode=ro', uri=True, timeout=0, check_same_thread=False)
            con.row_factory = sqlite3.Row
    except Exception as exc:
        raise _lock_error(target, exc) from exc
    try:
        _check_schema(con, target)
    except Exception:
        con.close()
        raise
    return con


def open_write(target: Target):
    """Open for writing without running schema DDL (the schema must already exist)."""
    try:
        if target.backend == 'duckdb':
            import duckdb
            con = duckdb.connect(str(target.path))
        else:
            con = sqlite3.connect(str(target.path), timeout=0, check_same_thread=False)
            con.row_factory = sqlite3.Row
            con.execute('PRAGMA foreign_keys=ON')
    except Exception as exc:
        raise _lock_error(target, exc) from exc
    try:
        _check_schema(con, target)
    except Exception:
        con.close()
        raise
    return con


def _rows(con, sql, params=()):
    # Deliberately on the connection itself: a DuckDB cursor() is a separate connection
    # and would not see (or be part of) the caller's open write transaction.
    cur = con.execute(sql, params)
    names = [c[0] for c in cur.description]
    return [dict(zip(names, r)) for r in cur.fetchall()]


def _one(con, sql, params=()):
    return next(iter(_rows(con, sql, params)), None)


def logical_state(con) -> dict:
    """What a selection change or import may alter, in a comparable form."""
    return {'selection': {r['source_id']: r['snapshot_id'] for r in
                          _rows(con, 'SELECT source_id,snapshot_id FROM selection_members ORDER BY source_id')},
            'snapshots': [[r['snapshot_id'], r['source_id'], r['content_sha256'], r['import_status']] for r in
                          _rows(con, 'SELECT snapshot_id,source_id,content_sha256,import_status FROM feed_snapshots ORDER BY snapshot_id')],
            'sources': [r['source_id'] for r in _rows(con, 'SELECT source_id FROM feed_sources ORDER BY source_id')]}


def sha256_file(path) -> str:
    with open(path, 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def resolve_archive(raw) -> Path:
    p = Path(raw)
    return p if p.is_absolute() else ROOT / p


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git_commit() -> str | None:
    try:
        return subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, capture_output=True, text=True,
                              timeout=10).stdout.strip() or None
    except Exception:
        return None


# -- inspection --------------------------------------------------------------------------

def archive_dirs(con) -> list[str]:
    """Archive roots used by existing snapshots (``<root>/<source>/<sha>.zip``), as stored."""
    seen = []
    for r in _rows(con, 'SELECT raw_zip_path FROM feed_snapshots ORDER BY snapshot_id'):
        parent = str(Path(r['raw_zip_path']).parent.parent)
        if parent not in seen:
            seen.append(parent)
    return seen


def status(target: Target, *, source_id: str | None = None, verify_archives: bool = False,
           extra_archive_dirs=()) -> dict:
    con = open_readonly(target)
    try:
        state = logical_state(con)
        sources = _rows(con, 'SELECT * FROM feed_sources ORDER BY source_id')
        if source_id is not None and source_id not in state['sources']:
            raise AdminError(f'Unknown source {source_id!r} in {target.path}. Known: {", ".join(state["sources"]) or "none"}')
        snaps = _rows(con, 'SELECT * FROM feed_snapshots ORDER BY source_id,retrieved_at,snapshot_id')
        dirs = archive_dirs(con)
    finally:
        con.close()
    out = {'database': target.describe(), 'sources': []}
    referenced = set()
    for src in sources:
        if source_id is not None and src['source_id'] != source_id:
            continue
        selected = state['selection'].get(src['source_id'])
        items = []
        for s in (x for x in snaps if x['source_id'] == src['source_id']):
            path = resolve_archive(s['raw_zip_path'])
            present = path.is_file()
            info = {}
            if present:
                try:
                    info = declared_info(str(path), s['content_sha256'])
                except (BadZipFile, OSError, UnicodeError):
                    info = {}
            sidecar = path.with_suffix('.zip.provenance.json')
            items.append({
                'snapshot_id': s['snapshot_id'], 'short_sha256': s['content_sha256'][:12],
                'content_sha256': s['content_sha256'], 'import_status': s['import_status'],
                'selected': s['snapshot_id'] == selected, 'retrieved_at': s['retrieved_at'],
                'coverage': {'start': s['feed_start'], 'end': s['feed_end']},
                'feed_version': info.get('feed_version') or None,
                'archive': {'path': s['raw_zip_path'], 'present': present,
                            'sha256_verified': (sha256_file(path) == s['content_sha256']) if present and verify_archives else None,
                            'provenance_sidecar': sidecar.is_file()}})
        out['sources'].append({'source_id': src['source_id'], 'publisher': src['publisher'],
                               'selected_snapshot_id': selected, 'snapshots': items})
    for s in snaps:
        referenced.add(resolve_archive(s['raw_zip_path']).resolve())
    orphans, partial = [], []
    for d in list(dict.fromkeys([*dirs, *map(str, extra_archive_dirs)])):
        base = resolve_archive(d)
        if not base.is_dir():
            continue
        for f in sorted(base.glob('*/*.zip')):
            if f.resolve() not in referenced:
                orphans.append(str(f))
        partial += [str(f) for f in sorted(base.glob('*/*.part'))]
    out['archive_dirs'] = dirs
    out['unreferenced_archives'] = orphans
    out['partial_files'] = partial
    return out


# -- backups ------------------------------------------------------------------------------

def _sidecars(target: Target) -> list[Path]:
    names = ['.wal'] if target.backend == 'duckdb' else ['-wal', '-journal']
    return [Path(str(target.path) + n) for n in names if Path(str(target.path) + n).exists()]


def _fsync(path: Path):
    with open(path, 'rb+') as stream:
        os.fsync(stream.fileno())


def create_backup(target: Target, backup_root, *, label: str, copy=shutil.copyfile) -> dict:
    """Consistent, verified cold copy. Fails closed: on any error nothing is left behind
    except an explanatory exception, and the caller must not mutate."""
    pending = _sidecars(target)
    if pending:
        raise AdminError(f'Uncheckpointed write-ahead/journal file present ({", ".join(map(str, pending))}); '
                         'refusing to back up or mutate. Open the database once with its owner to checkpoint it.')
    backup_root = Path(backup_root).resolve()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder = backup_root / f'{target.path.stem}-{stamp}-{uuid4().hex[:8]}'
    final = folder / target.path.name
    part = folder / (target.path.name + '.part')
    created_folder = False
    lock = None
    try:
        # Hold a lock that excludes writers for the whole copy: a read-only DuckDB
        # connection, or an exclusive SQLite transaction.
        if target.backend == 'duckdb':
            lock = open_readonly(target)
        else:
            lock = open_write(target)
            try:
                lock.execute('BEGIN EXCLUSIVE')
            except sqlite3.OperationalError as exc:
                raise _lock_error(target, exc) from exc
        state = logical_state(lock)
        before = sha256_file(target.path)
        folder.mkdir(parents=True, exist_ok=False)
        created_folder = True
        copy(target.path, part)
        _fsync(part)
        after = sha256_file(target.path)
        copied = sha256_file(part)
        if not (before == after == copied):
            raise AdminError('Backup copy does not match the source database bytes '
                             f'(source {before[:12]}→{after[:12]}, copy {copied[:12]}).')
        if lock is not None:
            if target.backend == 'sqlite':
                lock.rollback()
            lock.close()
            lock = None
        if final.exists():
            raise AdminError(f'Refusing to overwrite existing backup {final}')
        part.replace(final)
        verify = resolve_database(final, target.backend)
        check = open_readonly(verify)
        try:
            if target.backend == 'sqlite':
                result = _rows(check, 'PRAGMA integrity_check')
                if [list(r.values())[0] for r in result] != ['ok']:
                    raise AdminError(f'Backup integrity check failed: {result[:3]}')
            if logical_state(check) != state:
                raise AdminError('Backup content differs from the source database state.')
        finally:
            check.close()
        if sha256_file(final) != copied:
            raise AdminError('Backup bytes changed during verification.')
        meta = {'kind': 'transport-atlas-database-backup', 'label': label, 'created_at': _now(),
                'source_database': str(target.path), 'backend': target.backend,
                'backup_file': str(final), 'bytes': final.stat().st_size, 'sha256': copied,
                'source_mtime': datetime.fromtimestamp(target.path.stat().st_mtime, timezone.utc).isoformat(),
                'method': ('cold file copy under a read-only DuckDB connection (excludes writers)'
                           if target.backend == 'duckdb' else 'cold file copy under BEGIN EXCLUSIVE'),
                'verified': ['source bytes unchanged during copy', 'copy sha256 equals source sha256',
                             'backup opens read-only', 'selection, snapshots and sources equal the source'],
                'state': state, 'git_commit': _git_commit()}
        with open(folder / 'backup.json', 'x', encoding='utf-8') as stream:
            json.dump(meta, stream, indent=2)
        meta['metadata_file'] = str(folder / 'backup.json')
        return meta
    except Exception as exc:
        if lock is not None:
            try:
                lock.close()
            except Exception:
                pass
        if created_folder:
            for f in (part, final, folder / 'backup.json'):
                try:
                    f.unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                folder.rmdir()
            except OSError:
                pass
        if isinstance(exc, AdminError):
            raise AdminError(f'Backup failed; no change was made. {exc}') from exc
        raise AdminError(f'Backup failed; no change was made. {type(exc).__name__}: {exc}') from exc


def load_backup(metadata) -> dict:
    meta = json.loads(Path(metadata).read_text(encoding='utf-8'))
    if meta.get('kind') != 'transport-atlas-database-backup':
        raise AdminError(f'{metadata} is not a TransportAtlas backup record')
    return meta


def verify_backup(metadata) -> dict:
    """Read-only: recompute the backup hash and compare its state with the record."""
    meta = load_backup(metadata)
    path = Path(meta['backup_file'])
    if not path.is_file():
        raise AdminError(f'Backup file missing: {path}')
    digest = sha256_file(path)
    if digest != meta['sha256']:
        raise AdminError(f'Backup hash mismatch: recorded {meta["sha256"][:12]}, found {digest[:12]}')
    con = open_readonly(resolve_database(path, meta['backend']))
    try:
        if logical_state(con) != meta['state']:
            raise AdminError('Backup state differs from its record')
    finally:
        con.close()
    return {'backup_file': str(path), 'sha256': digest, 'verified': True, 'source_database': meta['source_database']}


def restore_backup(metadata, destination, *, apply: bool = False) -> dict:
    """Copy a verified backup to a NEW path. Never overwrites; never touches the live DB."""
    meta = load_backup(metadata)
    dest = Path(destination).expanduser().resolve()
    plan = {'backup_file': meta['backup_file'], 'sha256': meta['sha256'], 'destination': str(dest),
            'source_database': meta['source_database'], 'applied': False}
    if dest.exists():
        raise AdminError(f'Destination exists: {dest}. restore-backup never overwrites; choose a new path.')
    if dest == Path(meta['source_database']).resolve():
        raise AdminError('restore-backup will not write to the original database path; see the documented manual swap.')
    verify_backup(metadata)
    if not apply:
        return plan
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + f'.{uuid4().hex[:8]}.part')
    try:
        shutil.copyfile(meta['backup_file'], part)
        _fsync(part)
        if sha256_file(part) != meta['sha256']:
            raise AdminError('Restored copy hash mismatch')
        if dest.exists():
            raise AdminError(f'Destination appeared during restore: {dest}')
        part.replace(dest)
    finally:
        part.unlink(missing_ok=True)
    plan['applied'] = True
    return plan


# -- guarded mutation --------------------------------------------------------------------

def _require_services_stopped(services_record):
    if services_record is DEFAULT:
        services_record = SERVICES_RECORD
    if services_record is not None and Path(services_record).exists():
        raise AdminError(f'Local services are recorded as running ({services_record}). '
                         'Stop them with scripts/stop_local.ps1 before any database mutation.')


def guarded(target: Target, backup_root, label: str, mutate, *, services_record=DEFAULT,
            copy=shutil.copyfile) -> tuple[dict, object]:
    """Backup (verified) -> open for write -> confirm state unchanged -> mutate."""
    _require_services_stopped(services_record)
    meta = create_backup(target, backup_root, label=label, copy=copy)
    con = open_write(target)
    try:
        if logical_state(con) != meta['state']:
            raise AdminError('The database changed between the backup and opening it for writing; '
                             'aborted with no change. Re-run the command.')
        return meta, mutate(con)
    finally:
        con.close()


# -- selection changes -----------------------------------------------------------------

def _parse_id(value, what):
    if value is None or value == NONE:
        return None
    if not SNAPSHOT_RE.fullmatch(value):
        raise AdminError(f'{what} must be a full 32-character snapshot ID or "{NONE}"; got {value!r}')
    return value


def _check_source(source_id):
    if not SOURCE_RE.fullmatch(source_id or ''):
        raise AdminError(f'Invalid source_id {source_id!r}')


def _target_problems(con, source_id, snapshot_id, *, verify_archive=True) -> list[str]:
    if snapshot_id is None:
        return []
    snap = _one(con, 'SELECT * FROM feed_snapshots WHERE snapshot_id=?', (snapshot_id,))
    if snap is None:
        return [f'snapshot {snapshot_id} does not exist']
    problems = []
    if snap['source_id'] != source_id:
        problems.append(f'snapshot {snapshot_id} belongs to source {snap["source_id"]!r}, not {source_id!r}')
    if snap['import_status'] != 'ready':
        problems.append(f'snapshot {snapshot_id} has import_status {snap["import_status"]!r}, not ready')
    path = resolve_archive(snap['raw_zip_path'])
    if not path.is_file():
        problems.append(f'archive missing: {path}')
    elif verify_archive and sha256_file(path) != snap['content_sha256']:
        problems.append(f'archive hash mismatch: {path}')
    return problems


def plan_selection(target: Target, source_id: str, to, expected_current) -> dict:
    """Read-only preview of a selection change; ``problems`` empty means it may apply."""
    _check_source(source_id)
    to, expected = _parse_id(to, '--snapshot'), _parse_id(expected_current, '--expected-current')
    con = open_readonly(target)
    try:
        if _one(con, 'SELECT source_id FROM feed_sources WHERE source_id=?', (source_id,)) is None:
            raise AdminError(f'Unknown source {source_id!r} in {target.path}')
        current = (_one(con, 'SELECT snapshot_id FROM selection_members WHERE source_id=?', (source_id,)) or {}).get('snapshot_id')
        problems = _target_problems(con, source_id, to)
    finally:
        con.close()
    if current != expected:
        problems.append(f'stale --expected-current: current selection is {current or NONE}, '
                        f'expected {expected or NONE}')
    return {'database': target.describe(), 'source_id': source_id, 'current': current, 'expected_current': expected,
            'to': to, 'change': current != to, 'problems': problems}


def change_selection(con, source_id: str, to, expected_current, *, backend: str) -> dict:
    """One small transaction: re-check everything, then swap the pointer."""
    _check_source(source_id)
    to, expected = _parse_id(to, '--snapshot'), _parse_id(expected_current, '--expected-current')
    con.execute('BEGIN IMMEDIATE' if backend == 'sqlite' else 'BEGIN TRANSACTION')
    try:
        if _one(con, 'SELECT source_id FROM feed_sources WHERE source_id=?', (source_id,)) is None:
            raise AdminError(f'Unknown source {source_id!r}')
        current = (_one(con, 'SELECT snapshot_id FROM selection_members WHERE source_id=?', (source_id,)) or {}).get('snapshot_id')
        if current != expected:
            raise AdminError(f'Selection changed concurrently or --expected-current is stale: current is '
                             f'{current or NONE}, expected {expected or NONE}. Nothing was changed.')
        problems = _target_problems(con, source_id, to)
        if problems:
            raise AdminError('Refusing selection change: ' + '; '.join(problems))
        if current == to:
            con.rollback()
            return {'source_id': source_id, 'before': current, 'after': current, 'changed': False}
        con.execute('DELETE FROM selection_members WHERE source_id=?', (source_id,))
        if to is not None:
            con.execute('INSERT INTO selection_members VALUES(?,?)', (source_id, to))
        after = (_one(con, 'SELECT snapshot_id FROM selection_members WHERE source_id=?', (source_id,)) or {}).get('snapshot_id')
        if after != to:
            raise AdminError('Selection read-back mismatch; rolled back')
        con.commit()
    except Exception:
        con.rollback()
        raise
    return {'source_id': source_id, 'before': current, 'after': to, 'changed': True}


def _journal(backup_root) -> Path:
    return Path(backup_root).resolve() / JOURNAL


def journal_entries(backup_root, target: Target, source_id: str) -> list[dict]:
    path = _journal(backup_root)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding='utf-8').splitlines():
        if line.strip():
            e = json.loads(line)
            if e['database'] == str(target.path) and e['source_id'] == source_id:
                out.append(e)
    return out


def _append_journal(backup_root, entry):
    path = _journal(backup_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'a', encoding='utf-8') as stream:
        stream.write(json.dumps(entry) + '\n')
        stream.flush()
        os.fsync(stream.fileno())


def apply_selection(target: Target, source_id, to, expected_current, *, backup_root=DEFAULT_BACKUPS,
                    kind='activate', reverts=None, services_record=DEFAULT, copy=shutil.copyfile) -> dict:
    plan = plan_selection(target, source_id, to, expected_current)
    if plan['problems']:
        raise AdminError('Refusing selection change: ' + '; '.join(plan['problems']))
    if not plan['change']:
        return {**plan, 'applied': False, 'note': 'Already selected; nothing to do (no backup taken).'}
    op = uuid4().hex
    base = {'operation_id': op, 'kind': kind, 'database': str(target.path), 'source_id': source_id,
            'before': plan['current'], 'after': plan['to'], 'reverts': reverts}

    def mutate(con):
        _append_journal(backup_root, {**base, 'phase': 'intent', 'at': _now()})
        try:
            result = change_selection(con, source_id, to, expected_current, backend=target.backend)
        except Exception as exc:
            _append_journal(backup_root, {**base, 'phase': 'aborted', 'at': _now(), 'error': str(exc)[:300]})
            raise
        _append_journal(backup_root, {**base, 'phase': 'applied', 'at': _now()})
        return result

    meta, result = guarded(target, backup_root, f'{kind} {source_id}', mutate,
                           services_record=services_record, copy=copy)
    return {**plan, 'applied': True, 'operation_id': op, 'result': result,
            'backup': {k: meta[k] for k in ('backup_file', 'sha256', 'metadata_file')}}


def plan_restore(target: Target, source_id: str, backup_root=DEFAULT_BACKUPS) -> dict:
    """Undo the most recent journaled change of this source, if the DB still shows it."""
    _check_source(source_id)
    entries = journal_entries(backup_root, target, source_id)
    if not entries:
        raise AdminError(f'No recorded selection change for {source_id!r} on {target.path} in {_journal(backup_root)}')
    outcome = {e['operation_id']: e['phase'] for e in entries if e['phase'] != 'intent'}
    dangling = [e for e in entries if e['phase'] == 'intent' and e['operation_id'] not in outcome]
    if dangling:
        raise AdminError(f'Journal has an operation without a recorded outcome ({dangling[-1]["operation_id"]}); '
                         'inspect `status` and use `activate` with --expected-current explicitly.')
    applied = [e for e in entries if e['phase'] == 'applied']
    if not applied:
        raise AdminError(f'No applied selection change for {source_id!r} to restore')
    last = applied[-1]
    plan = plan_selection(target, source_id, last['before'] or NONE, last['after'] or NONE)
    plan['reverts'] = last['operation_id']
    plan['recorded_change'] = {'before': last['before'], 'after': last['after'], 'at': last['at']}
    return plan


def apply_restore(target: Target, source_id: str, *, backup_root=DEFAULT_BACKUPS,
                  services_record=DEFAULT) -> dict:
    plan = plan_restore(target, source_id, backup_root)
    if plan['problems']:
        raise AdminError('Refusing restore: ' + '; '.join(plan['problems']))
    return apply_selection(target, source_id, plan['to'] or NONE, plan['current'] or NONE, backup_root=backup_root,
                           kind='restore', reverts=plan['reverts'], services_record=services_record)


# -- import without activation ---------------------------------------------------------------

def _provenance_problems(prov: dict, source_id: str, digest: str, size: int, entry: dict | None) -> list[str]:
    problems = []
    if prov.get('source_id') != source_id:
        problems.append(f'provenance source_id {prov.get("source_id")!r} != {source_id!r}')
    if prov.get('sha256') != digest:
        problems.append('provenance sha256 does not match the ZIP')
    if prov.get('bytes') is not None and prov.get('bytes') != size:
        problems.append('provenance byte count does not match the ZIP')
    try:
        if datetime.fromisoformat(prov['retrieved_at']).utcoffset() is None:
            problems.append('provenance retrieved_at lacks a timezone')
    except (KeyError, TypeError, ValueError):
        problems.append('provenance retrieved_at missing or invalid')
    if entry is None:
        problems.append(f'{source_id!r} is not in the official catalog; real provenance cannot be checked')
        return problems
    try:
        safe_url(prov.get('download_url') or '', allowed_hosts=ALLOWED_OFFICIAL_HOSTS | set(entry.get('download_hosts', [])))
    except FeedError as exc:
        problems.append(f'provenance download_url rejected: {exc}')
    if prov.get('landing_url') != entry['landing_url']:
        problems.append('provenance landing_url differs from the official catalog')
    return problems


def plan_import(target: Target, zip_path, source_id: str, *, archive_dir=None, new_source=False,
                synthetic=False, catalog_data=None) -> dict:
    """Read-only preview: verify the ZIP and its provenance, decide new vs existing."""
    _check_source(source_id)
    zip_path = Path(zip_path).expanduser().resolve()
    if not zip_path.is_file():
        raise AdminError(f'ZIP not found: {zip_path}. Only existing local files are accepted (no downloads).')
    entries = (catalog_data or catalog())['sources']
    entry = entries.get(source_id)
    problems = []
    digest, size = sha256_file(zip_path), zip_path.stat().st_size
    try:
        with ZipFile(zip_path) as z:
            bad = z.testzip()
        if bad:
            problems.append(f'ZIP CRC failure in member {bad}')
    except (BadZipFile, OSError) as exc:
        problems.append(f'not a readable ZIP: {exc}')
    counts = None
    if not problems:
        try:
            data = read_feed(zip_path)
            _validate(data)
            counts = {name: len(data[name]) for name in data if name != 'feed_info'}
        except FeedError as exc:
            problems.append(f'GTFS validation failed: {exc}')
    sidecar = zip_path.with_suffix(zip_path.suffix + '.provenance.json')
    provenance = None
    if synthetic:
        if entry is not None:
            problems.append(f'{source_id!r} is an official catalog source; --synthetic is not allowed for it')
        if sidecar.exists():
            problems.append('--synthetic given but a provenance sidecar exists; refusing the contradiction')
    elif not sidecar.exists():
        problems.append(f'no provenance sidecar {sidecar.name}; real feeds require one '
                        '(use --synthetic only for invented test fixtures)')
    else:
        try:
            provenance = json.loads(sidecar.read_text(encoding='utf-8-sig'))
            problems += _provenance_problems(provenance, source_id, digest, size, entry)
        except ValueError as exc:
            problems.append(f'provenance sidecar unreadable: {exc}')
    con = open_readonly(target)
    try:
        state = logical_state(con)
        source = _one(con, 'SELECT * FROM feed_sources WHERE source_id=?', (source_id,))
        existing = _one(con, 'SELECT snapshot_id,import_status FROM feed_snapshots WHERE source_id=? AND content_sha256=?',
                        (source_id, digest))
        dirs = archive_dirs(con)
    finally:
        con.close()
    if source is None and not new_source:
        problems.append(f'source {source_id!r} is not in this database '
                        f'(known: {", ".join(state["sources"]) or "none"}); pass --new-source if intended')
    if source is not None and new_source:
        problems.append(f'--new-source given but {source_id!r} already exists')
    if synthetic and source is not None and not str(source['publisher']).startswith('SYNTHETIC'):
        problems.append(f'source {source_id!r} is not a synthetic source; --synthetic refused')
    if source is None and not synthetic and entry is None:
        problems.append(f'new source {source_id!r} is neither in the official catalog nor --synthetic')
    if archive_dir is None:
        if len(dirs) == 1:
            archive_dir = dirs[0]
        else:
            problems.append(f'cannot infer the archive directory (found {dirs or "none"}); pass --archive-dir')
    snapshot_id = hashlib.sha256((source_id + ':' + digest).encode()).hexdigest()[:32]
    archive = resolve_archive(Path(archive_dir) / source_id / (digest + '.zip')) if archive_dir is not None else None
    archive_state = None
    if archive is not None:
        if not archive.exists():
            archive_state = 'will be created'
        elif sha256_file(archive) == digest:
            archive_state = 'exists with identical bytes (reused, not modified)'
        else:
            archive_state = 'exists with DIFFERENT bytes'
            problems.append(f'archive path {archive} holds different bytes; refusing')
        side = archive.with_suffix('.zip.provenance.json')
        if provenance is not None and side.exists():
            try:
                if json.loads(side.read_text(encoding='utf-8')) != provenance:
                    problems.append(f'archived provenance {side} differs from the sidecar; refusing')
            except ValueError:
                problems.append(f'archived provenance {side} is unreadable; refusing')
    if existing and existing['import_status'] != 'ready':
        problems.append(f'a {existing["import_status"]} snapshot with these bytes already exists ({existing["snapshot_id"]})')
    action = 'existing' if existing and existing['import_status'] == 'ready' else 'import'
    return {'database': target.describe(), 'source_id': source_id, 'zip': str(zip_path), 'sha256': digest,
            'bytes': size, 'synthetic': synthetic, 'provenance': provenance, 'counts': counts,
            'new_source': source is None, 'action': action,
            'snapshot_id': existing['snapshot_id'] if existing else snapshot_id,
            'archive_dir': None if archive_dir is None else str(archive_dir),
            'archive': None if archive is None else str(archive), 'archive_state': archive_state,
            'selection': {'current': state['selection'].get(source_id), 'after': state['selection'].get(source_id),
                          'policy': 'preserved: import never changes the current selection; use `activate`'},
            'publisher': (source['publisher'] if source else SYNTHETIC_PUBLISHER if synthetic
                          else entry['publisher'] if entry else None),
            'problems': problems}


def apply_import(target: Target, zip_path, source_id: str, *, archive_dir=None, new_source=False, synthetic=False,
                 backup_root=DEFAULT_BACKUPS, services_record=DEFAULT, catalog_data=None,
                 copy=shutil.copyfile, importer=import_file) -> dict:
    plan = plan_import(target, zip_path, source_id, archive_dir=archive_dir, new_source=new_source,
                       synthetic=synthetic, catalog_data=catalog_data)
    if plan['problems']:
        raise AdminError('Refusing import: ' + '; '.join(plan['problems']))
    if plan['action'] == 'existing':
        return {**plan, 'applied': False, 'note': 'Identical bytes already imported as a ready snapshot; '
                'nothing to do (no backup taken, selection untouched).'}
    entry = (catalog_data or catalog())['sources'].get(source_id) or {}

    def mutate(con):
        before = logical_state(con)
        # Stored archive paths follow the database's existing convention; a relative
        # archive root (the real DB uses data\raw) is relative to the repository root.
        with contextlib.chdir(ROOT):
            result = importer(con, plan['zip'], source_id=source_id, archive_dir=plan['archive_dir'],
                              publisher=plan['publisher'], landing_url=entry.get('landing_url'), activate=False)
        after = logical_state(con)
        if after['selection'] != before['selection']:
            raise AdminError('INVARIANT VIOLATION: selection changed during a preserve-selection import; '
                             'restore from the backup listed above.')
        snap = _one(con, 'SELECT import_status,content_sha256 FROM feed_snapshots WHERE snapshot_id=?', (result['snapshot_id'],))
        if snap is None or snap['import_status'] != 'ready' or snap['content_sha256'] != plan['sha256']:
            raise AdminError('Imported snapshot is not ready or hash differs')
        return result

    meta, result = guarded(target, backup_root, f'import {source_id}', mutate,
                           services_record=services_record, copy=copy)
    archive = resolve_archive(result.get('archive_path') or plan['archive'])
    if sha256_file(archive) != plan['sha256']:
        raise AdminError(f'Archived file hash mismatch after import: {archive}')
    return {**plan, 'applied': True, 'result': result,
            'backup': {k: meta[k] for k in ('backup_file', 'sha256', 'metadata_file')}}
