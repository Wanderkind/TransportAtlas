"""Disposable, snapshot-addressed serialized map responses; no provider data edits."""
import hashlib
import json
import logging
import os
import time
from pathlib import Path
from uuid import uuid4

FORMAT_VERSION = 'regional-coverage-v3'
# Least-recently-used bounds for disposable response files; override with
# TRANSPORT_ATLAS_NETWORK_CACHE_FILES / TRANSPORT_ATLAS_NETWORK_CACHE_BYTES.
DEFAULT_MAX_FILES = 500
DEFAULT_MAX_BYTES = 1024**3
STALE_PART_SECONDS = 3600


def _limit(name, default):
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


def prune(folder, *, max_files=None, max_bytes=None, keep=None, now=None):
    """Delete least-recently-used cached responses beyond the bounds.

    Only ``<64 hex>.json`` files and abandoned ``.part`` temporaries in this
    cache folder are considered. ``keep`` (a key) is never removed. Returns the
    removed file names.
    """
    max_files = max_files or _limit('TRANSPORT_ATLAS_NETWORK_CACHE_FILES', DEFAULT_MAX_FILES)
    max_bytes = max_bytes or _limit('TRANSPORT_ATLAS_NETWORK_CACHE_BYTES', DEFAULT_MAX_BYTES)
    now = time.time() if now is None else now
    removed, entries = [], []
    try:
        candidates = list(Path(folder).iterdir())
    except OSError:
        return removed
    for path in candidates:
        try:
            stat = path.stat()
        except OSError:
            continue
        if path.suffix == '.part' and now-stat.st_mtime > STALE_PART_SECONDS:
            try:
                path.unlink(); removed.append(path.name)
            except OSError:
                pass
        elif path.suffix == '.json' and len(path.stem) == 64 and all(c in '0123456789abcdef' for c in path.stem):
            entries.append((stat.st_mtime, stat.st_size, path))
    entries.sort(key=lambda item: item[0])
    count, total = len(entries), sum(size for _, size, _ in entries)
    for _, size, path in entries:
        if count <= max_files and total <= max_bytes:
            break
        if keep is not None and path.stem == keep:
            continue
        try:
            path.unlink()
        except OSError:
            continue
        removed.append(path.name); count -= 1; total -= size
    return removed


def cache_key(selection, day, mode, source, sources, active_only, max_routes, max_points):
    value = [FORMAT_VERSION, selection, day.isoformat(), mode, source, sources, active_only, max_routes, max_points]
    return hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()


def read_cache(folder, key):
    try:
        payload = (Path(folder)/(key+'.json')).read_bytes()
        data = json.loads(payload)
        if data.get('type') == 'FeatureCollection' and isinstance(data.get('features'), list) and data.get('metadata',{}).get('cache_key') == key:
            try:
                os.utime(Path(folder)/(key+'.json'))  # recency for LRU pruning
            except OSError:
                pass
            return payload
    except (OSError, ValueError, AttributeError):
        pass
    return None


def write_cache(folder, key, data):
    data['metadata']['cache_key'] = key
    payload = json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')
    folder = Path(folder)
    temporary = folder/(key+'.'+uuid4().hex+'.part')
    try:
        folder.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(payload)
        temporary.replace(folder/(key+'.json'))
        prune(folder, keep=key)
    except OSError:
        logging.getLogger(__name__).warning('Map response cache could not be written; serving uncached data.')
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return payload
