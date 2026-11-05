"""Parse bounded GTFS Schedule ZIPs without extracting arbitrary paths."""
from __future__ import annotations
import csv
from datetime import date
from io import TextIOWrapper
from pathlib import Path, PurePosixPath
from zipfile import ZipFile, BadZipFile

MAX_COMPRESSED = 150 * 1024 * 1024
MAX_UNCOMPRESSED = 700 * 1024 * 1024
MAX_FILES = 150
MAX_ROWS_PER_TABLE = 2_500_000
TABLES = (
 "agency", "routes", "stops", "trips", "stop_times", "calendar",
 "calendar_dates", "shapes", "frequencies", "feed_info",
)

class FeedError(ValueError):
    """A feed is invalid or unsupported by this initial importer."""

def service_date(raw: str) -> str:
    if len(raw) != 8 or not raw.isdigit():
        raise FeedError(f"Invalid GTFS date: {raw!r}")
    return date(int(raw[:4]),int(raw[4:6]),int(raw[6:])).isoformat()

def clock_seconds(raw: str) -> int | None:
    if raw is None or raw.strip() == "":
        return None
    parts = raw.strip().split(":")
    if len(parts) != 3 or not all(p.isdecimal() for p in parts):
        raise FeedError(f"Invalid GTFS time: {raw!r}")
    h,m,s = map(int,parts)
    if m >= 60 or s >= 60:
        raise FeedError(f"Invalid GTFS time: {raw!r}")
    return h*3600+m*60+s

def optional_int(value: str | None, default=None):
    return int(value) if value not in (None, "") else default

def optional_float(value: str | None):
    return float(value) if value not in (None, "") else None

def read_feed(filename: Path) -> dict[str,list[dict[str,str]]]:
    if filename.stat().st_size > MAX_COMPRESSED:
        raise FeedError("ZIP file exceeds compressed size cap")
    try:
        with ZipFile(filename) as z:
            members = z.infolist()
            if len(members) > MAX_FILES:
                raise FeedError("Too many ZIP members")
            size = 0
            for m in members:
                p = PurePosixPath(m.filename.replace('\\','/'))
                if (m.filename.startswith(('/', '\\')) or '..' in p.parts or
                    len(p.parts) != 1 or (m.external_attr >> 16) & 0o170000 == 0o120000):
                    raise FeedError(f"Unsafe ZIP member: {m.filename}")
                size += m.file_size
                if size > MAX_UNCOMPRESSED:
                    raise FeedError("ZIP exceeds expanded size cap")
            all_names = {m.filename.lower():m.filename for m in members if not m.is_dir()}
            results = {}
            for name in TABLES:
                member_name = all_names.get(name+".txt")
                if not member_name:
                    results[name] = []
                    continue
                with z.open(member_name) as buf:
                    with TextIOWrapper(buf, encoding="utf-8-sig", newline="") as txt:
                        reader = csv.DictReader(txt)
                        if not reader.fieldnames or any(h is None for h in reader.fieldnames):
                            raise FeedError(f"Invalid header in {name}.txt")
                        records=[]
                        for row in reader:
                            if None in row:
                                raise FeedError(f"Malformed CSV row in {name}.txt")
                            records.append({str(k): (v or "") for k,v in row.items()})
                            if len(records) > MAX_ROWS_PER_TABLE:
                                raise FeedError(f"Too many rows in {name}.txt")
                        results[name] = records
    except (BadZipFile, UnicodeError, UnicodeDecodeError) as exc:
        raise FeedError(f"Unreadable GTFS ZIP: {exc}") from exc
    required = ("agency","routes","stops","trips","stop_times")
    for name in required:
        if not results[name]:
            raise FeedError(f"Missing or empty required {name}.txt")
    if not results["calendar"] and not results["calendar_dates"]:
        raise FeedError("At least one calendar or calendar_dates table required")
    return results
