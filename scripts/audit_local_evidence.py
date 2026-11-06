"""Offline independent audit: existing CSV ZIPs against a read-only DuckDB.

Does not import application code, download, reimport, alter the database, or clear
caches. Run only with the backend stopped (DuckDB's interprocess lock applies).
Provenance URLs/timestamps are recorded local evidence, not a fresh HTTP observation.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
from datetime import date, datetime, timezone
import hashlib
from io import TextIOWrapper
import json
from pathlib import Path
from urllib.parse import urlsplit
from zipfile import ZipFile

DATES = ('2026-11-06', '2026-11-07', '2026-11-08', '2026-11-09')
WEEK = ('monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday')
TABLES = {
    'agency': ('agencies', ('agency_id',)),
    'routes': ('routes', ('route_id',)), 'stops': ('stops', ('stop_id',)),
    'trips': ('trips', ('trip_id',)),
    'stop_times': ('stop_times', ('trip_id', 'stop_sequence')),
    'calendar': ('calendars', ('service_id',)),
    'calendar_dates': ('calendar_exceptions', ('service_id', 'date')),
    'shapes': ('shapes', ('shape_id', 'shape_pt_sequence')),
    'frequencies': ('frequencies', ('trip_id', 'start_time')),
}
NORMALIZED = {
    'agency': 'agency_id agency_name agency_url agency_timezone',
    'routes': 'route_id agency_id route_short_name route_long_name route_type route_color',
    'stops': 'stop_id stop_name stop_lat stop_lon location_type parent_station stop_timezone',
    'trips': 'route_id service_id trip_id shape_id direction_id trip_headsign block_id',
    'stop_times': 'trip_id stop_sequence stop_id arrival_time departure_time pickup_type drop_off_type timepoint',
    'calendar': 'service_id monday tuesday wednesday thursday friday saturday sunday start_date end_date',
    'calendar_dates': 'service_id date exception_type',
    'shapes': 'shape_id shape_pt_lat shape_pt_lon shape_pt_sequence',
    'frequencies': 'trip_id start_time end_time headway_secs exact_times',
}


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def sha(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def records(con, sql, params=()):
    cursor = con.execute(sql, params)
    names = [c[0] for c in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def raw_day(calendars, exceptions, trips, frequencies, day):
    compact = day.replace('-', '')
    wd = WEEK[date.fromisoformat(day).weekday()]
    active = {r['service_id'] for r in calendars
              if r['start_date'] <= compact <= r['end_date'] and r[wd] == '1'}
    additions = removals = 0
    for r in exceptions:
        if r['date'] == compact:
            if r['exception_type'] == '1':
                active.add(r['service_id'])
                additions += 1
            else:
                active.discard(r['service_id'])
                removals += 1
    selected = [t for t in trips if t['service_id'] in active]
    scheduled = windows = 0
    for trip in selected:
        freq = frequencies.get(trip['trip_id'], [])
        if not freq:
            scheduled += 1
        for f in freq:
            if f.get('exact_times', '') == '1':
                seconds = lambda v: sum(int(x) * m for x, m in zip(v.split(':'), (3600, 60, 1)))
                length = seconds(f['end_time']) - seconds(f['start_time'])
                scheduled += (length + int(f['headway_secs']) - 1) // int(f['headway_secs'])
            else:
                windows += 1
    return dict(active_services=len(active), active_trip_templates=len(selected),
                active_route_records=len({t['route_id'] for t in selected}),
                scheduled_trip_instances=scheduled, non_exact_frequency_windows=windows,
                exception_additions=additions, exception_removals=removals)


def sql_day(con, sid, day):
    # Independent set operations in SQL, with frequencies aggregated per trip.
    wd = WEEK[date.fromisoformat(day).weekday()]
    sql = f'''WITH active AS (
      (SELECT service_id FROM calendars WHERE snapshot_id=? AND start_date<=?
       AND end_date>=? AND {wd}=1 UNION SELECT service_id FROM calendar_exceptions
       WHERE snapshot_id=? AND service_date=? AND exception_type=1)
      EXCEPT SELECT service_id FROM calendar_exceptions
       WHERE snapshot_id=? AND service_date=? AND exception_type=2),
      f AS (SELECT trip_id, COUNT(*) n,
       SUM(CASE WHEN exact_times=1 THEN CEIL((end_sec-start_sec)*1.0/headway_secs) ELSE 0 END) exact_n,
       SUM(CASE WHEN exact_times=0 THEN 1 ELSE 0 END) windows
       FROM frequencies WHERE snapshot_id=? GROUP BY trip_id)
      SELECT COUNT(*) active_trip_templates, COUNT(DISTINCT t.route_id) active_route_records,
       COALESCE(SUM(CASE WHEN f.n IS NULL THEN 1 ELSE f.exact_n END),0) scheduled_trip_instances,
       COALESCE(SUM(f.windows),0) non_exact_frequency_windows
       FROM trips t JOIN active a ON t.service_id=a.service_id
       LEFT JOIN f ON f.trip_id=t.trip_id WHERE t.snapshot_id=?'''
    return records(con, sql, (sid, day, day, sid, day, sid, day, sid, sid))[0]


def audit_metadata(root, output):
    """Small repeatable raw-only supplement; safe while the backend is running."""
    result = {'method': 'Raw CSV date parsing and archived sidecar comparison; no database connection',
              'sources': [], 'passed': True}
    for source in load(root / 'configs/sardinia_sources.json')['sources']:
        downloaded = root / 'data/downloads' / (source + '.zip')
        provenance = load(downloaded.with_suffix('.zip.provenance.json'))
        archived = root / 'data/raw' / source / (provenance['sha256'] + '.zip.provenance.json')
        item = dict(source_id=source, archived_provenance=str(archived),
                    archived_provenance_matches_download=load(archived)==provenance,
                    valid_calendar_dates=0, valid_exception_dates=0, calendar_rows=0,
                    declared_feed_info=[])
        observed = []
        with ZipFile(downloaded) as z:
            for name in z.namelist():
                stem = Path(name).stem
                if stem not in ('calendar','calendar_dates','feed_info'):
                    continue
                with z.open(name) as stream:
                    for row in csv.DictReader(TextIOWrapper(stream,encoding='utf-8-sig',newline='')):
                        if stem=='feed_info':
                            item['declared_feed_info'].append(row)
                            continue
                        values = (row['start_date'],row['end_date']) if stem=='calendar' else (row['date'],)
                        for value in values:
                            parsed = datetime.strptime(value,'%Y%m%d').date()
                            if parsed.strftime('%Y%m%d') != value:
                                raise ValueError(f'Noncanonical GTFS date: {value}')
                            observed.append(parsed)
                        if stem=='calendar':
                            item['valid_calendar_dates'] += len(values)
                            item['calendar_rows'] += 1
                        else:
                            item['valid_exception_dates'] += 1
        item['observed_start'] = min(observed).isoformat()
        item['observed_end'] = max(observed).isoformat()
        result['passed'] &= item['archived_provenance_matches_download']
        result['sources'].append(item)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result),flush=True)
    return 0 if result['passed'] else 1


def audit(root, db, output):
    import duckdb
    report = {'generated_at': datetime.now(timezone.utc).isoformat(),
              'method': 'Independent csv.DictReader + independent SQL; no application imports; DuckDB read_only=True',
              'retrieval_evidence_limit': 'URLs and retrieval timestamps are recorded local provenance; HTTP download was not independently re-observed.',
              'sources': [], 'checks': [], 'discrepancies': []}
    def check(name, actual, expected):
        item = dict(check=name, actual=actual, expected=expected, passed=actual == expected)
        report['checks'].append(item)
        if not item['passed']:
            report['discrepancies'].append(item)
    old = {x['source_id']: x for x in load(root / 'data/local-verification/feeds.json')}
    fields = {x['source_id']: x for x in load(root / 'data/local-verification/field-audit.json')}
    coverage = load(root / 'data/local-verification/route-coverage.json')
    coverage_sources = {x['source_id']: x for x in coverage['sources']}
    manifest = load(root / 'configs/sardinia_sources.json')['sources']
    con = duckdb.connect(str(db), read_only=True)
    try:
        con.execute('SET threads=2')
        snapshots = records(con, 'SELECT s.* FROM feed_snapshots s JOIN selection_members m ON s.snapshot_id=m.snapshot_id ORDER BY s.source_id')
        check('selected official source set', sorted(s['source_id'] for s in snapshots), sorted(manifest))
        check('selection source matches snapshot source', con.execute('SELECT COUNT(*) FROM selection_members m JOIN feed_snapshots s ON m.snapshot_id=s.snapshot_id WHERE m.source_id<>s.source_id').fetchone()[0], 0)
        for snapshot in snapshots:
            source, sid = snapshot['source_id'], snapshot['snapshot_id']
            print(f'Auditing {source}', flush=True)
            prefix = source + ': '
            archive = Path(snapshot['raw_zip_path'])
            if not archive.is_absolute():
                archive = root / archive
            downloaded = root / 'data/downloads' / (source + '.zip')
            provenance = load(downloaded.with_suffix('.zip.provenance.json'))
            digest = sha(archive)
            item = dict(source_id=source, snapshot_id=sid, archive=str(archive), sha256=digest,
                        provenance=provenance, snapshot=snapshot, raw_tables={}, dates={})
            report['sources'].append(item)
            for label, expected in [('snapshot SHA256', snapshot['content_sha256']), ('provenance SHA256', provenance['sha256']), ('download SHA256', sha(downloaded)), ('previous feed SHA256', old[source]['provenance']['sha256'])]:
                check(prefix + label, digest, expected)
            check(prefix+'recorded download bytes', downloaded.stat().st_size, provenance['bytes'])
            check(prefix+'recorded source', provenance['source_id'], source)
            timestamp = datetime.fromisoformat(provenance['retrieved_at'])
            check(prefix+'timezone-aware retrieval timestamp', timestamp.utcoffset() is not None, True)
            item['retrieval_to_import_seconds'] = (datetime.fromisoformat(snapshot['retrieved_at']) - timestamp).total_seconds()
            url = urlsplit(provenance['download_url'])
            approved = {'sardegnamobilita.it', 'www.sardegnamobilita.it', *manifest[source].get('download_hosts', [])}
            check(prefix+'approved HTTPS download host', url.scheme=='https' and url.hostname in approved and not url.username and not url.password and url.port in (None,443), True)
            check(prefix+'official landing URL', provenance['landing_url'], manifest[source]['landing_url'])
            data = {}; shape_counts = Counter(); stop_coords = {}; fallback_sequences = defaultdict(list)
            with ZipFile(archive) as z:
                check(prefix+'ZIP CRC', z.testzip(), None)
                # Shapes first, then trips before stop_times so fallback is computed streaming.
                names = sorted((n for n in z.namelist() if n.lower().endswith('.txt')), key=lambda n: (0 if Path(n).stem=='shapes' else 1 if Path(n).stem in ('trips','stops') else 2, n))
                for name in names:
                    stem = Path(name).stem
                    keys = TABLES.get(stem, (None, ()))[1]
                    seen = set(); duplicate = count = 0
                    keep = stem not in ('shapes', 'stop_times')
                    saved = []
                    with z.open(name) as stream:
                        reader = csv.DictReader(TextIOWrapper(stream, encoding='utf-8-sig', newline=''))
                        columns = reader.fieldnames
                        for row in reader:
                            count += 1
                            if keys:
                                key = tuple(row.get(k, '') for k in keys)
                                duplicate += key in seen
                                seen.add(key)
                            if keep: saved.append(row)
                            if stem=='shapes': shape_counts[row['shape_id']] += 1
                            if stem=='stops': stop_coords[row['stop_id']] = (row.get('stop_lon'),row.get('stop_lat'))
                            if stem=='stop_times' and row['trip_id'] in fallback_ids:
                                coords = stop_coords.get(row['stop_id'])
                                if coords and all(coords): fallback_sequences[row['trip_id']].append((int(row['stop_sequence']),coords))
                    item['raw_tables'][name] = dict(rows=count, columns=columns, duplicate_primary_keys=duplicate)
                    if keys: check(prefix+stem+' raw unique keys', duplicate, 0)
                    if keep: data[stem] = saved
                    if stem=='trips': fallback_ids = {t['trip_id'] for t in saved if shape_counts[t.get('shape_id','')]<2}
                    if stem in TABLES:
                        table = TABLES[stem][0]
                        sql_count = con.execute(f'SELECT COUNT(*) FROM {table} WHERE snapshot_id=?', (sid,)).fetchone()[0]
                        item['raw_tables'][name]['sql_rows'] = sql_count
                        check(prefix+stem+' CSV/SQL rows', count, sql_count)
                    previous = old[source]['raw_tables'].get(name)
                    check(prefix+name+' previous counts/columns', dict(rows=count,columns=columns), previous)
            omitted = {n:[c for c in info['columns'] if c not in NORMALIZED.get(Path(n).stem,'').split()] for n,info in item['raw_tables'].items()}
            omitted = {n:c for n,c in omitted.items() if c}
            check(prefix+'previous field audit', omitted, fields[source]['fields_not_normalized'])
            check(prefix+'previous declared feed_info', data.get('feed_info',[]), fields[source].get('declared_feed_info',[]))
            calendars, exceptions, trips = data.get('calendar',[]), data.get('calendar_dates',[]), data['trips']
            for row in calendars:
                date.fromisoformat(f"{row['start_date'][:4]}-{row['start_date'][4:6]}-{row['start_date'][6:]}")
                date.fromisoformat(f"{row['end_date'][:4]}-{row['end_date'][4:6]}-{row['end_date'][6:]}")
            check(prefix+'valid calendar ranges/weekday flags', all(r['start_date']<=r['end_date'] and all(r[w] in ('0','1') for w in WEEK) for r in calendars), True)
            check(prefix+'valid exception types', all(r['exception_type'] in ('1','2') for r in exceptions), True)
            item['calendar_bounds'] = dict(start=min((r['start_date'] for r in calendars),default=None), end=max((r['end_date'] for r in calendars),default=None), exception_dates_min=min((r['date'] for r in exceptions),default=None), exception_dates_max=max((r['date'] for r in exceptions),default=None), exception_types=dict(Counter(r['exception_type'] for r in exceptions)))
            freq = defaultdict(list)
            for f in data.get('frequencies',[]): freq[f['trip_id']].append(f)
            for day in DATES:
                raw = raw_day(calendars,exceptions,trips,freq,day); sql = sql_day(con,sid,day)
                item['dates'][day] = dict(raw=raw,sql=sql)
                for key,value in sql.items():
                    check(prefix+day+' '+key+' raw/SQL', raw[key], value)
                    check(prefix+day+' '+key+' prior feed report', raw[key], old[source]['dates'][day][key])
            route_ids = {r['route_id'] for r in data['routes']}; referenced = {t['route_id'] for t in trips}
            unused = sorted(route_ids-referenced)
            item['routes_without_trips'] = unused
            check(prefix+'routes without trips prior coverage', unused, coverage_sources[source]['routes_without_trips'])
            sql_unused = [r[0] for r in con.execute('SELECT route_id FROM routes r WHERE snapshot_id=? AND NOT EXISTS (SELECT 1 FROM trips t WHERE t.snapshot_id=r.snapshot_id AND t.route_id=r.route_id) ORDER BY route_id',(sid,)).fetchall()]
            check(prefix+'routes without trips raw/SQL', unused, sql_unused)
            missing = {t['trip_id'] for t in trips if not t.get('shape_id') or not shape_counts[t['shape_id']]}
            qa = {r[0] for r in con.execute("SELECT entity_id FROM qa_issues WHERE snapshot_id=? AND issue_code='missing_shape'",(sid,)).fetchall()}
            check(prefix+'missing shape trip warnings', sorted(missing), sorted(qa))
            signatures = {(next(t['route_id'] for t in trips if t['trip_id']==tid),tuple(coords for _,coords in sorted(sequence))) for tid,sequence in fallback_sequences.items() if len(sequence)>=2}
            item['geometry'] = dict(missing_shape_trip_warnings=len(missing), fallback_trip_templates=len(fallback_ids), fallback_features=len(signatures), fallback_routes=sorted({s[0] for s in signatures}), insufficient_shape_point_trips=len(fallback_ids-missing))
            for child,parent,join in [('trips','routes','c.route_id=p.route_id'),('stop_times','trips','c.trip_id=p.trip_id'),('stop_times','stops','c.stop_id=p.stop_id')]:
                invalid = con.execute(f'SELECT COUNT(*) FROM {child} c WHERE c.snapshot_id=? AND NOT EXISTS (SELECT 1 FROM {parent} p WHERE p.snapshot_id=c.snapshot_id AND {join})',(sid,)).fetchone()[0]
                check(prefix+child+' source-scoped '+parent+' references',invalid,0)
        network = load(root/'data/local-verification/network-all-imported.json')
        props = [f['properties'] for f in network['features']]
        report['previous_network_summary'] = dict(metadata=network['metadata'],quality_counts=dict(Counter(p['quality'] for p in props)))
        for item in report['sources']:
            source = item['source_id']
            schematic = [p for p in props if p['source_id']==source and p['quality']=='schematic-stop-link']
            check(source+': previous network schematic features',len(schematic),item['geometry']['fallback_features'])
            check(source+': previous network schematic route set',sorted({p['route_id'] for p in schematic}),item['geometry']['fallback_routes'])
        check('previous network route count',len({(p['snapshot_id'],p['route_id']) for p in props}),sum(s['raw_tables']['routes.txt']['rows']-len(s['routes_without_trips']) for s in report['sources']))
        check('previous geometry metadata consistency',network['metadata'],coverage['all_imported_geometry'])
        report['totals'] = dict(routes=sum(s['raw_tables']['routes.txt']['rows'] for s in report['sources']), routes_without_trips=sum(len(s['routes_without_trips']) for s in report['sources']), missing_shape_trip_warnings=sum(s['geometry']['missing_shape_trip_warnings'] for s in report['sources']), schematic_features=sum(s['geometry']['fallback_features'] for s in report['sources']))
    except BaseException as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        con.close()
        report['passed'] = not report['discrepancies'] and 'error' not in report
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report,indent=2,default=str),encoding='utf-8')
    print(json.dumps(dict(passed=report['passed'], totals=report.get('totals'), discrepancies=len(report['discrepancies']), output=str(output))),flush=True)
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parents[1])
    parser.add_argument('--db',type=Path,default=Path('data/transport_atlas.duckdb'))
    parser.add_argument('--output',type=Path,default=Path('data/local-verification/phase3/independent-data-audit.json'))
    parser.add_argument('--metadata-only',action='store_true',help='Only parse every raw calendar date and compare provenance sidecars; does not open DuckDB')
    args=parser.parse_args()
    if args.metadata_only:
        output = args.root / 'data/local-verification/phase3/independent-raw-metadata.json'
        raise SystemExit(audit_metadata(args.root,output))
    raise SystemExit(audit(args.root,args.root/args.db,args.root/args.output))
