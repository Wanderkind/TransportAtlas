"""Resumable official imports into one local DB; reports stay under ignored data/."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
from pathlib import Path
import time
from zipfile import ZipFile

from transport_atlas.official import catalog, download
from transport_atlas.ingest import import_file
from transport_atlas.schedule import daily_summary
from transport_atlas.storage import connect, rows, one

# Columns represented in the normalized DB. All other provider fields remain
# recoverable from the immutable raw ZIP, but are not used in analyses.
NORMALIZED_COLUMNS = {
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


def inspect_zip(path):
    with ZipFile(path) as z:
        bad = z.testzip()
        if bad:
            raise ValueError(f'ZIP CRC failure: {bad}')
        tables = {}
        for name in z.namelist():
            if name.lower().endswith('.txt'):
                with z.open(name) as stream:
                    reader = csv.DictReader(io.TextIOWrapper(stream, encoding='utf-8-sig', newline=''))
                    tables[name] = {'rows': sum(1 for _ in reader), 'columns': reader.fieldnames}
        return tables


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sources', default='aspo,trenitalia_sardegna,arst,atp_sassari')
    p.add_argument('--db', type=Path, default=Path('data/transport_atlas.duckdb'))
    p.add_argument('--offline', action='store_true', help='Never download missing feeds')
    p.add_argument('--report', default='feeds.json', help='Report basename under data/local-verification')
    args = p.parse_args()
    root = Path('data/local-verification')
    root.mkdir(parents=True, exist_ok=True)
    results = []
    for source in args.sources.split(','):
        source = source.strip()
        entry = catalog()['sources'][source]
        item = {'source_id': source, 'status': 'not_verified'}
        started = time.perf_counter()
        try:
            path = Path('data/downloads') / f'{source}.zip'
            if not path.exists():
                if args.offline:
                    raise ValueError('No archived download available in offline mode')
                item['download'] = download(source, path)
            provenance = json.loads(path.with_suffix('.zip.provenance.json').read_text(encoding='utf-8'))
            if provenance['source_id'] != source or hashlib.sha256(path.read_bytes()).hexdigest() != provenance['sha256']:
                raise ValueError('Download provenance/hash mismatch; refusing import')
            item['provenance'] = provenance
            item['raw_tables'] = inspect_zip(path)
            item['optional_fields_not_normalized'] = {
                table: [c for c in info['columns'] if c not in NORMALIZED_COLUMNS.get(table.removesuffix('.txt'), '').split()]
                for table, info in item['raw_tables'].items()
            }
            with ZipFile(path) as z:
                if 'feed_info.txt' in z.namelist():
                    with z.open('feed_info.txt') as stream:
                        item['declared_feed_info'] = list(csv.DictReader(io.TextIOWrapper(stream, encoding='utf-8-sig', newline='')))
            print(f"{source}: ZIP integrity verified; importing into {args.db}", flush=True)
            con = connect(args.db)
            try:
                t = time.perf_counter()
                result = import_file(con, path, source_id=source, archive_dir='data/raw',
                                     publisher=entry['publisher'], landing_url=entry['landing_url'])
                item['import_seconds'] = round(time.perf_counter() - t, 3)
                item['import'] = result
                sid = result['snapshot_id']
                item['counts'] = {}
                names = {'agency': 'agencies', 'calendar': 'calendars', 'calendar_dates': 'calendar_exceptions'}
                for table, raw in item['raw_tables'].items():
                    name = table.removesuffix('.txt')
                    if name not in {'agency', 'routes', 'stops', 'trips', 'stop_times', 'calendar', 'calendar_dates', 'shapes', 'frequencies'}:
                        continue
                    imported = one(con, f'SELECT COUNT(*) AS n FROM {names.get(name, name)} WHERE snapshot_id=?', (sid,))['n']
                    item['counts'][name] = {'raw': raw['rows'], 'database': imported}
                    if raw['rows'] != imported:
                        raise ValueError(f'Raw/import count mismatch in {name}')
                item['warnings'] = rows(con, 'SELECT issue_code,COUNT(*) AS n FROM qa_issues WHERE snapshot_id=? GROUP BY issue_code', (sid,))
                item['dates'] = {}
                for day in ('2026-11-06', '2026-11-07', '2026-11-08', '2026-11-09'):
                    from datetime import date
                    item['dates'][day] = next(s for s in daily_summary(con, date.fromisoformat(day))['snapshots'] if s['snapshot_id'] == sid)
                item['coexisting_sources'] = rows(con, 'SELECT source_id,snapshot_id FROM selection_members ORDER BY source_id')
                item['status'] = 'verified'
            finally:
                con.close()
        except Exception as exc:
            item['error'] = f'{type(exc).__name__}: {exc}'
        item['total_seconds'] = round(time.perf_counter() - started, 3)
        results.append(item)
        (root / Path(args.report).name).write_text(json.dumps(results, indent=2, default=str), encoding='utf-8')
        print(json.dumps(item, default=str), flush=True)
    print(f'Evidence: {root / Path(args.report).name}', flush=True)
    return 0 if all(item['status'] == 'verified' for item in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
