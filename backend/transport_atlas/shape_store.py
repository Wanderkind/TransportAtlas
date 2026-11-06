"""Disposable on-disk store of simplified shape display levels, per snapshot.

Imported snapshots are immutable and addressed by ``snapshot_id`` plus archive
SHA-256, so the simplified base geometry and the coarser levels chosen by the
regional point budget can be reused across server restarts. Files live in the
ignored cache folder and are validated on read; any mismatch, corruption or
version change falls back to recomputation. Nothing here edits feed data.

Layout per snapshot: one compacted ``<snapshot_id>.json`` plus small
``<snapshot_id>--<uuid>.json`` delta files holding only what a later build
learned. Loading merges deltas and compacts them once they accumulate, so a
build never rewrites the whole (tens of MB) document.
"""
from __future__ import annotations
import json
import logging
import time
from pathlib import Path
from uuid import uuid4

FORMAT_VERSION = 'shape-levels-v1'
# Bump when simplify_line or its base tolerance changes, invalidating old files.
ALGORITHM = 'radial-half-then-douglas-peucker-half/base-0.0001'
COMPACT_AFTER_DELTAS = 8
STALE_PART_SECONDS = 3600


def _main(folder: Path, snapshot_id: str) -> Path:
    return Path(folder)/f'{snapshot_id}.json'


def _deltas(folder: Path, snapshot_id: str) -> list[Path]:
    try:
        return sorted(Path(folder).glob(f'{snapshot_id}--*.json'))
    except OSError:
        return []


def inventory(shape_cache: dict, snapshot_id: str) -> dict:
    """What the process holds for one snapshot: shape id -> stored levels."""
    return {shape_id: frozenset(entry[2]) for (sid, shape_id), entry in list(shape_cache.items()) if sid == snapshot_id}


def _read(path: Path, snapshot: dict) -> dict | None:
    try:
        data = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    if (isinstance(data, dict) and data.get('format') == FORMAT_VERSION and data.get('algorithm') == ALGORITHM
            and data.get('snapshot_id') == snapshot['snapshot_id']
            and data.get('content_sha256') == snapshot['content_sha256'] and isinstance(data.get('shapes'), dict)):
        return data['shapes']
    return None


def _merge(shape_cache: dict, sid: str, shapes: dict, limit: int) -> bool:
    """Merge stored shapes; return False if the cache limit left some unloaded."""
    complete = True
    for shape_id, item in shapes.items():
        try:
            count, base, levels = item
            levels = {float(k): v for k, v in levels.items()}
        except (TypeError, ValueError, AttributeError):
            continue
        existing = shape_cache.get((sid, shape_id))
        if existing is not None:
            for tolerance, coords in levels.items():
                existing[2].setdefault(tolerance, coords)
        elif len(shape_cache) < limit:
            shape_cache[(sid, shape_id)] = (int(count), base, levels)
        else:
            complete = False
    return complete


def _document(sid: str, snapshot: dict, shapes: dict) -> dict:
    return {'format': FORMAT_VERSION, 'algorithm': ALGORITHM, 'snapshot_id': sid,
            'content_sha256': snapshot['content_sha256'], 'shapes': shapes}


def _entry(entry) -> list:
    return [entry[0], entry[1], {repr(k): v for k, v in list(entry[2].items())}]


def hydrate(shape_cache: dict, folder: Path, snapshot: dict, *, limit: int) -> dict:
    """Load a snapshot's stored levels into the process cache; return its inventory."""
    sid = snapshot['snapshot_id']
    _remove_stale_parts(folder, sid)
    complete = True
    main = _read(_main(folder, sid), snapshot)
    if main is not None:
        complete = _merge(shape_cache, sid, main, limit) and complete
    deltas = _deltas(folder, sid)
    for path in deltas:
        shapes = _read(path, snapshot)
        if shapes is not None:
            complete = _merge(shape_cache, sid, shapes, limit) and complete
    # Compact only when everything stored is in memory; otherwise the rewritten
    # main file would drop shapes that did not fit in the process cache.
    if complete and len(deltas) >= COMPACT_AFTER_DELTAS:
        shapes = {shape_id: _entry(entry) for (s, shape_id), entry in list(shape_cache.items()) if s == sid}
        if write(_main(folder, sid), _document(sid, snapshot, shapes)):
            for path in deltas:
                try:
                    path.unlink()
                except OSError:
                    pass
    return inventory(shape_cache, sid)


def _remove_stale_parts(folder: Path, snapshot_id: str):
    """Delete temporaries abandoned by an interrupted writer (never read)."""
    try:
        parts = list(Path(folder).glob(f'{snapshot_id}*.part'))
    except OSError:
        return
    now = time.time()
    for path in parts:
        try:
            if now-path.stat().st_mtime > STALE_PART_SECONDS:
                path.unlink()
        except OSError:
            pass


def capture(shape_cache: dict, snapshot: dict, before: dict) -> tuple[str, dict] | None:
    """Describe what a build added since ``before``; call while holding the cache lock.

    Returns a delta file name and document. The document references coordinate
    lists that are never mutated after creation, so another thread may write it.
    """
    sid = snapshot['snapshot_id']
    added = {}
    for (s, shape_id), entry in list(shape_cache.items()):
        if s != sid:
            continue
        known = before.get(shape_id)
        if known is None:
            added[shape_id] = _entry(entry)
        else:
            new_levels = {repr(k): v for k, v in list(entry[2].items()) if k not in known}
            if new_levels:
                added[shape_id] = [entry[0], entry[1], new_levels]
    if not added:
        return None
    return f'{sid}--{uuid4().hex}.json', _document(sid, snapshot, added)


def write(target: Path, document: dict) -> bool:
    """Serialize and atomically create or replace one store file."""
    target = Path(target)
    temporary = target.with_name(f'{target.stem}.{uuid4().hex}.part')
    try:
        # Non-finite coordinates are not valid JSON; skip storing rather than fail a request.
        payload = json.dumps(document, separators=(',', ':'), allow_nan=False).encode('utf-8')
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(payload)
        temporary.replace(target)
        return True
    except (OSError, ValueError, TypeError):
        logging.getLogger(__name__).warning('Shape level cache could not be written; continuing in memory.')
        return False
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def persist(shape_cache: dict, folder: Path, snapshot: dict, before: dict) -> bool:
    """Synchronously store what was learned since ``before`` as a delta file."""
    captured = capture(shape_cache, snapshot, before)
    return write(Path(folder)/captured[0], captured[1]) if captured is not None else False
