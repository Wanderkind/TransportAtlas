"""Small local HTTP latency sample, including serialized geometry payload size."""
import json
import os
import argparse
from pathlib import Path
import statistics
import time
from urllib.parse import quote
from urllib.request import urlopen


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=Path('data/local-verification/phase3/performance.json'))
    parser.add_argument('--samples',type=int,default=5)
    parser.add_argument('--geometry-only',action='store_true',help='Measure regional geometry without unrelated API warm-up requests')
    parser.add_argument('--all-imported',action='store_true',help='Include inactive trip-referenced routes in the geometry sample')
    args=parser.parse_args()
    if not 1<=args.samples<=20:parser.error('--samples must be between 1 and 20')
    report = {}
    def request(path):
        start = time.perf_counter()
        with urlopen(f"http://127.0.0.1:{os.environ.get('TRANSPORT_ATLAS_API_PORT') or 8000}/api/v1/"+path, timeout=180) as response:
            raw = response.read()
        return json.loads(raw), (time.perf_counter()-start)*1000, len(raw)
    paths=['network/geometry?date=2026-11-06&active_only='+('false' if args.all_imported else 'true')]
    if not args.geometry_only:
        routes, _, _ = request('routes?date=2026-11-06')
        stops, _, _ = request('stops?source=aspo&date=2026-11-06&active_only=true')
        route = next(r for r in routes['routes'] if r['source_id']=='aspo' and r['active_on_date'])
        stop = stops['stops'][0]
        paths = ['summary?date=2026-11-06', 'stops?date=2026-11-06&active_only=true', 'network/geometry?date=2026-11-06',
             f"route/{quote(route['snapshot_id'],safe='')}/{quote(route['route_id'],safe='')}/timetable?date=2026-11-06",
             f"stop/{quote(stop['snapshot_id'],safe='')}/{quote(stop['stop_id'],safe='')}/departures?date=2026-11-06"]
    for path in paths:
        samples = []
        for _ in range(args.samples):
            result, elapsed, size = request(path)
            samples.append(round(elapsed, 2))
        report[path] = {'samples_ms': samples, 'median_ms': round(statistics.median(samples), 2),
                        'max_ms': max(samples), 'bytes': size}
        if 'metadata' in result:
            report[path]['metadata'] = result['metadata']
        print(path, report[path], flush=True)
    report['note'] = f'{args.samples} sequential loopback HTTP requests per endpoint; max is not a statistically robust p95. Timings include serialization.'
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2),encoding='utf-8')


if __name__=='__main__':
    main()
