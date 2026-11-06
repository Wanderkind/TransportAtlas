"""Immutable GTFS ingestion with source-scoped IDs and transactional activation."""
from __future__ import annotations
import hashlib
import json
import re
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from .gtfs import FeedError, clock_seconds, optional_int, optional_float, read_feed, service_date
from .storage import one, insert_many

WEEKDAYS = ("monday","tuesday","wednesday","thursday","friday","saturday","sunday")

def _unique(rows, key, table):
    ids = [r[key] for r in rows]
    if not all(ids) or len(set(ids)) != len(ids):
        raise FeedError(f"Duplicate or missing {key} in {table}")
    return set(ids)

def _validate(data):
    aids = []
    for n,a in enumerate(data['agency']):
        aids.append(a.get('agency_id','').strip())
        if not a.get('agency_name') or not a.get('agency_timezone'):
            raise FeedError("agency_name and agency_timezone are required")
    if len(aids) == 1 and not aids[0]:
        data['agency'][0]['agency_id'] = '__single_agency__'
        aids = ['__single_agency__']
    if len(set(aids)) != len(aids) or any(not a for a in aids):
        raise FeedError("Multiagency feed requires distinct agency_id values")
    route_ids = _unique(data['routes'],'route_id','routes')
    stop_ids = _unique(data['stops'],'stop_id','stops')
    trip_ids = _unique(data['trips'],'trip_id','trips')
    calendar_ids = _unique(data['calendar'],'service_id','calendar') if data['calendar'] else set()
    exception_ids = {a['service_id'] for a in data['calendar_dates']}
    for row in data['routes']:
        aid = row.get('agency_id') or (aids[0] if len(aids)==1 else None)
        if aid not in aids: raise FeedError(f"Unknown agency_id on route {row['route_id']}")
        row['agency_id'] = aid
        if not row.get('route_type'): raise FeedError("Missing route_type")
    for row in data['stops']:
        lat, lon = optional_float(row.get('stop_lat')), optional_float(row.get('stop_lon'))
        if lat is not None and not (-90 <= lat <= 90):
            raise FeedError("Stop latitude outside valid range")
        if lon is not None and not (-180 <= lon <= 180):
            raise FeedError("Stop longitude outside valid range")
    for row in data['trips']:
        if row.get('route_id') not in route_ids: raise FeedError("Trip references unknown route")
        if row.get('service_id') not in calendar_ids | exception_ids: raise FeedError("Trip references unknown service")
    seen = set()
    visit_count = defaultdict(int)
    for row in data['stop_times']:
        if row.get('trip_id') not in trip_ids: raise FeedError("Stop time references unknown trip")
        if row.get('stop_id') not in stop_ids: raise FeedError("Stop time references unknown stop")
        key = (row['trip_id'], optional_int(row['stop_sequence']))
        if key[1] is None or key in seen: raise FeedError("Duplicate or missing stop_sequence")
        seen.add(key)
        visit_count[row['trip_id']]+=1
        clock_seconds(row.get('arrival_time'))
        clock_seconds(row.get('departure_time'))
    if any(visit_count[t] < 2 for t in trip_ids):
        raise FeedError("Each trip must have at least two stop_times")
    for c in data['calendar']:
        if service_date(c['start_date']) > service_date(c['end_date']):
            raise FeedError("Calendar start_date exceeds end_date")
        for d in WEEKDAYS:
            if c.get(d) not in ('0','1'): raise FeedError(f"Invalid calendar weekday {d}")
    seen_cd = set()
    for c in data['calendar_dates']:
        k=(c['service_id'], service_date(c['date']))
        if k in seen_cd or c.get('exception_type') not in ('1','2'):
            raise FeedError("Invalid or duplicate calendar exception")
        seen_cd.add(k)
    shape_ids = {a['shape_id'] for a in data['shapes']}
    for sh in data['shapes']:
        if not (-90 <= float(sh['shape_pt_lat']) <= 90 and -180 <= float(sh['shape_pt_lon']) <= 180):
            raise FeedError("Shape coordinate outside valid range")
    seen_freq = set()
    intervals = defaultdict(list)
    for f in data['frequencies']:
        tid = f['trip_id']
        if tid not in trip_ids: raise FeedError("Frequency references unknown trip")
        s,e = clock_seconds(f['start_time']), clock_seconds(f['end_time'])
        h=optional_int(f.get('headway_secs'))
        if s is None or e is None or e <= s or h is None or h <= 0:
            raise FeedError("Invalid frequency interval")
        if f.get('exact_times','0') not in ('','0','1'):
            raise FeedError("Invalid exact_times flag")
        if (tid,s) in seen_freq: raise FeedError("Duplicate frequency period")
        seen_freq.add((tid,s))
        intervals[tid].append((s,e))
    for tid, seq in intervals.items():
        seq.sort()
        if any(a[1] > b[0] for a,b in zip(seq,seq[1:])):
            raise FeedError(f"Overlapping frequency intervals for trip {tid}")
    return shape_ids

def import_file(con, file: str | Path, *, source_id: str, archive_dir: str | Path,
                publisher: str | None = None, landing_url: str | None = None,
                license_name: str | None = None) -> dict:
    """Parse+validate before BEGIN, commit source snapshot and active pointer atomically."""
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', source_id):
        raise FeedError("source_id must contain safe ASCII letters, numbers, _ or -")
    file = Path(file)
    h=hashlib.sha256()
    with file.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1048576), b''):
            h.update(chunk)
    digest=h.hexdigest()
    provenance_file = file.with_suffix(file.suffix + '.provenance.json')
    provenance = None
    if provenance_file.exists():
        provenance = json.loads(provenance_file.read_text(encoding='utf-8'))
        if provenance.get('source_id') != source_id or provenance.get('sha256') != digest:
            raise FeedError('Download provenance does not match source or ZIP checksum')
        retrieved = datetime.fromisoformat(provenance['retrieved_at'])
        if retrieved.tzinfo is None:
            raise FeedError('Download provenance retrieval timestamp requires a timezone')
    existing=one(con,"SELECT snapshot_id FROM feed_snapshots WHERE source_id=? AND content_sha256=? AND import_status='ready'",(source_id,digest))
    if existing:
        return dict(status="unchanged",snapshot_id=existing['snapshot_id'],source_id=source_id,sha256=digest)
    data=read_feed(file)
    known_shapes=_validate(data)
    snapshot_id = hashlib.sha256((source_id+':'+digest).encode()).hexdigest()[:32]
    archive_path = Path(archive_dir)/source_id/(digest+'.zip')
    archive_path.parent.mkdir(parents=True,exist_ok=True)
    if archive_path.exists() and hashlib.sha256(archive_path.read_bytes()).hexdigest()!=digest:
        raise FeedError('Existing archive path has mismatched content; refusing to overwrite')
    if not archive_path.exists():
        temporary=archive_path.with_name(archive_path.name+'.'+uuid4().hex+'.part')
        try:
            shutil.copyfile(file,temporary)
            if hashlib.sha256(temporary.read_bytes()).hexdigest()!=digest:
                raise FeedError("Archived bytes did not match source hash")
            temporary.replace(archive_path)
        finally:
            temporary.unlink(missing_ok=True)
    if provenance is not None:
        archive_path.with_suffix('.zip.provenance.json').write_text(
            json.dumps(provenance, indent=2), encoding='utf-8')
    calendar_dates = [service_date(r['start_date']) for r in data['calendar']] + [service_date(r['end_date']) for r in data['calendar']] + [service_date(r['date']) for r in data['calendar_dates']]
    feed_start=min(calendar_dates)
    feed_end=max(calendar_dates)
    timezone_set={a['agency_timezone'] for a in data['agency']}
    tz=next(iter(timezone_set)) if len(timezone_set)==1 else None
    begin = 'BEGIN TRANSACTION'
    try:
        con.execute(begin)
        if one(con,'SELECT source_id FROM feed_sources WHERE source_id=?',(source_id,)) is None:
            con.execute('INSERT INTO feed_sources VALUES(?,?,?,?,?)',
                        (source_id,publisher or source_id,landing_url,None,license_name))
        if provenance is not None:
            con.execute('UPDATE feed_sources SET download_url=? WHERE source_id=?',
                        (provenance.get('download_url'), source_id))
        con.execute('INSERT INTO feed_snapshots VALUES(?,?,?,?,?,?,?,?,?)',
                    (snapshot_id,source_id,digest,provenance['retrieved_at'] if provenance else datetime.now(timezone.utc).isoformat(),str(archive_path),'ready',tz,feed_start,feed_end))
        def insert(table,stmt,objects):
            insert_many(con, table, stmt, objects)
        insert('agencies','INSERT INTO agencies VALUES(?,?,?,?,?)', [
            (snapshot_id,a['agency_id'],a['agency_name'],a.get('agency_url'),a['agency_timezone']) for a in data['agency']])
        insert('routes','INSERT INTO routes VALUES(?,?,?,?,?,?,?)', [
            (snapshot_id,r['route_id'],r['agency_id'],r.get('route_short_name'),r.get('route_long_name'),int(r['route_type']),r.get('route_color')) for r in data['routes']])
        insert('stops','INSERT INTO stops VALUES(?,?,?,?,?,?,?,?)', [
            (snapshot_id,s['stop_id'],s.get('stop_name'),optional_float(s.get('stop_lat')),optional_float(s.get('stop_lon')),
             optional_int(s.get('location_type'),0),s.get('parent_station'),s.get('stop_timezone')) for s in data['stops']])
        insert('trips','INSERT INTO trips VALUES(?,?,?,?,?,?,?,?)', [
            (snapshot_id,t['trip_id'],t['route_id'],t['service_id'],t.get('shape_id'),optional_int(t.get('direction_id')),
             t.get('trip_headsign'),t.get('block_id')) for t in data['trips']])
        insert('stop_times','INSERT INTO stop_times VALUES(?,?,?,?,?,?,?,?,?)', [
            (snapshot_id,s['trip_id'],int(s['stop_sequence']),s['stop_id'],clock_seconds(s.get('arrival_time')),
             clock_seconds(s.get('departure_time')),optional_int(s.get('pickup_type'),0),
             optional_int(s.get('drop_off_type'),0),optional_int(s.get('timepoint'))) for s in data['stop_times']])
        insert('calendars','INSERT INTO calendars VALUES(?,?,?,?,?,?,?,?,?,?,?)', [
            (snapshot_id,c['service_id'],*(int(c[d]) for d in WEEKDAYS),service_date(c['start_date']),service_date(c['end_date'])) for c in data['calendar']])
        insert('calendar_exceptions','INSERT INTO calendar_exceptions VALUES(?,?,?,?)', [
            (snapshot_id,c['service_id'],service_date(c['date']),int(c['exception_type'])) for c in data['calendar_dates']])
        insert('shapes','INSERT INTO shapes VALUES(?,?,?,?,?)', [
            (snapshot_id,sh['shape_id'],int(sh['shape_pt_sequence']),float(sh['shape_pt_lat']),float(sh['shape_pt_lon'])) for sh in data['shapes']])
        insert('frequencies','INSERT INTO frequencies VALUES(?,?,?,?,?,?)', [
            (snapshot_id,f['trip_id'],clock_seconds(f['start_time']),clock_seconds(f['end_time']),int(f['headway_secs']),int(f.get('exact_times') or '0')) for f in data['frequencies']])
        issues = [(snapshot_id,'missing_shape','warning',t['trip_id'],'Trip has missing GTFS shape geometry')
                  for t in data['trips'] if not t.get('shape_id') or t['shape_id'] not in known_shapes]
        insert('qa_issues','INSERT INTO qa_issues VALUES(?,?,?,?,?)',issues)
        con.execute('DELETE FROM selection_members WHERE source_id=?',(source_id,))
        con.execute('INSERT INTO selection_members VALUES(?,?)',(source_id,snapshot_id))
        con.commit()
    except Exception:
        con.rollback()
        raise
    return dict(status='imported',source_id=source_id,snapshot_id=snapshot_id,sha256=digest,
                feeds=dict((name,len(data[name])) for name in data if name != 'feed_info'),warnings=len(issues),
                service_start=feed_start,service_end=feed_end)
