"""Disposable, snapshot-addressed serialized map responses; no provider data edits."""
import hashlib
import json
import logging
from pathlib import Path
from uuid import uuid4

FORMAT_VERSION = 'regional-coverage-v3'


def cache_key(selection, day, mode, source, sources, active_only, max_routes, max_points):
    value = [FORMAT_VERSION, selection, day.isoformat(), mode, source, sources, active_only, max_routes, max_points]
    return hashlib.sha256(json.dumps(value, separators=(',', ':')).encode()).hexdigest()


def read_cache(folder, key):
    try:
        payload = (Path(folder)/(key+'.json')).read_bytes()
        data = json.loads(payload)
        if data.get('type') == 'FeatureCollection' and isinstance(data.get('features'), list) and data.get('metadata',{}).get('cache_key') == key:
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
    except OSError:
        logging.getLogger(__name__).warning('Map response cache could not be written; serving uncached data.')
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return payload
