"""Declared publisher validity remains separate from observed calendar coverage."""
import csv
from functools import lru_cache
from io import TextIOWrapper
from pathlib import Path
from zipfile import ZipFile
from .gtfs import service_date


@lru_cache(maxsize=128)
def declared_info(raw_zip_path, checksum):
    if not Path(raw_zip_path).exists():
        return {}
    with ZipFile(raw_zip_path) as z:
        name = next((n for n in z.namelist() if n.lower()=='feed_info.txt'), None)
        if name:
            with z.open(name) as stream:
                return next(csv.DictReader(TextIOWrapper(stream, encoding='utf-8-sig', newline='')), {})
    return {}


@lru_cache(maxsize=128)
def feed_info_present(raw_zip_path, checksum):
    """Whether the archive contains feed_info.txt (distinct from an empty one)."""
    if not Path(raw_zip_path).exists():
        return False
    with ZipFile(raw_zip_path) as z:
        return any(n.lower() == 'feed_info.txt' for n in z.namelist())


def declared_bounds(snapshot):
    info = declared_info(snapshot['raw_zip_path'], snapshot['content_sha256'])
    bounds = []
    for field in ('feed_start_date', 'feed_end_date'):
        try:
            bounds.append(service_date(info[field]) if info.get(field) else None)
        except (ValueError, KeyError):
            bounds.append(None)
    return bounds
