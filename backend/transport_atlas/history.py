"""Read-only inventory and comparison of archived feed snapshots of one source.

Nothing here reads or writes ``selection_members`` except to *report* which
snapshot is current, so the four-source atlas is never changed by browsing
history. Snapshots are immutable (``snapshot_id`` derives from source and
archive SHA-256), so per-snapshot facts are cached by snapshot ID and
comparisons by every input that affects them.

Three things are never conflated:

* **retrieval time** (``feed_snapshots.retrieved_at``): when this copy was downloaded;
* **feed version** (``feed_info.feed_version``, publisher text) and declared validity;
* **service date** (a GTFS service day evaluated against calendars + exceptions).

Identity: records are matched only by their GTFS ID *within one source*.
A matching ID is a feed-record match, not proof of the same physical route or
stop; a different ID is a record change, not proof of a real-world addition or
removal.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date
from functools import lru_cache
import hashlib
from pathlib import Path
from threading import Lock

from .nearby import distance_m
from .provenance import declared_info, feed_info_present
from .quality import validation_record, version_date
from .schedule import active_services
from .storage import one, rows

ROUTE_FIELDS = ('agency_id', 'short_name', 'long_name', 'route_type', 'route_color', 'headsigns')
STOP_FIELDS = ('name', 'location_type', 'parent_station')
# Coordinates equal within this distance are treated as unchanged (float noise).
COORDINATE_TOLERANCE_M = 1.0
RECORD_STATUSES = ('added', 'removed', 'modified', 'unchanged')
POLICIES = ('same_date', 'different_dates')

IDENTITY_NOTE = ('Records are matched by GTFS ID within this one source. An unchanged ID is a feed-record match, '
                 'not proof of the same physical route or stop; a new or missing ID is a feed-record change, not '
                 'proof that service or infrastructure was added or removed in reality.')


class HistoryError(ValueError):
    """Invalid comparison request (maps to HTTP 422)."""


class NotFound(KeyError):
    """Unknown source or snapshot (maps to HTTP 404)."""


def _pct(base, new):
    if base is None or new is None:
        return None
    if base == 0:
        return None  # undefined: zero baseline (reported via percent_note)
    return round(100*(new-base)/base, 1)


def _delta(base, new, *, attributable: bool):
    """Metric comparison; an unavailable side makes it incomparable, never zero."""
    if base is None or new is None:
        return {'baseline': base, 'comparison': new, 'delta': None, 'percent_change': None,
                'status': 'incomparable', 'percent_note': 'A side is unavailable for its service date.'}
    note = 'Percent change relative to the baseline value.' if base else 'Undefined: the baseline is zero.'
    return {'baseline': base, 'comparison': new, 'delta': new-base, 'percent_change': _pct(base, new),
            'status': 'unchanged' if new == base else 'changed', 'percent_note': note,
            'attributable_to_feed_version': attributable}


class SnapshotHistory:
    """Per-app history service with caches keyed by immutable snapshot IDs."""

    def __init__(self, con, *, validation_folder: Path | None = None):
        self.con = con
        self.validation_folder = validation_folder
        self._static: dict[str, dict] = {}
        self._shapes: dict[str, dict[str, str]] = {}
        self._lock = Lock()
        self._compare_cache: dict[tuple, dict] = {}
        self.compare_builds = 0  # observable for cache-identity tests

    # -- catalog -----------------------------------------------------------------
    def sources(self) -> list[dict]:
        selected = {r['source_id']: r['snapshot_id'] for r in rows(self.con, 'SELECT * FROM selection_members')}
        out = []
        for source in rows(self.con, 'SELECT source_id,publisher FROM feed_sources ORDER BY source_id'):
            snaps = rows(self.con, 'SELECT import_status FROM feed_snapshots WHERE source_id=?', (source['source_id'],))
            ready = sum(s['import_status'] == 'ready' for s in snaps)
            out.append(dict(source, snapshots=len(snaps), ready_snapshots=ready,
                            selected_snapshot_id=selected.get(source['source_id']),
                            comparison_possible=ready >= 2))
        return out

    def snapshot(self, snapshot_id: str) -> dict:
        snap = one(self.con, 'SELECT * FROM feed_snapshots WHERE snapshot_id=?', (snapshot_id,))
        if snap is None:
            raise NotFound(f'Unknown snapshot {snapshot_id}')
        return snap

    def inventory(self, source_id: str, service_day: date | None = None, *, verify_archives: bool = False) -> dict:
        if one(self.con, 'SELECT source_id FROM feed_sources WHERE source_id=?', (source_id,)) is None:
            raise NotFound(f'Unknown source {source_id}')
        selected = one(self.con, 'SELECT snapshot_id FROM selection_members WHERE source_id=?', (source_id,))
        snaps = rows(self.con, 'SELECT * FROM feed_snapshots WHERE source_id=? ORDER BY retrieved_at,snapshot_id', (source_id,))
        items = [self._describe(s, selected and selected['snapshot_id'], service_day, verify_archives) for s in snaps]
        ready = [i for i in items if i['import_status'] == 'ready']
        if len(ready) < 2:
            note = (f'Only {len(ready)} ready snapshot of this source is archived and imported, so no version '
                    'comparison is possible. Importing another genuine archived feed version is required.')
        else:
            note = f'{len(ready)} ready snapshots can be compared.'
        return {'source_id': source_id, 'service_date': service_day.isoformat() if service_day else None,
                'snapshots': items, 'ready_snapshots': len(ready), 'comparison_possible': len(ready) >= 2,
                'note': note, 'identity_note': IDENTITY_NOTE}

    def _describe(self, snap: dict, selected_id, service_day, verify) -> dict:
        sid = snap['snapshot_id']
        path = Path(snap['raw_zip_path'])
        present = path.is_file()
        archive = {'path': str(path), 'present': present, 'bytes': path.stat().st_size if present else None,
                   'sha256_verified': None}
        if present and verify:
            archive['sha256_verified'] = _file_sha256(str(path), path.stat().st_size, path.stat().st_mtime_ns) == snap['content_sha256']
        has_info = present and feed_info_present(str(path), snap['content_sha256'])
        info = declared_info(str(path), snap['content_sha256']) if has_info else {}
        record = (validation_record(self.validation_folder, snap['content_sha256'])
                  if self.validation_folder else None)
        item = {
            'snapshot_id': sid, 'source_id': snap['source_id'], 'content_sha256': snap['content_sha256'],
            'import_status': snap['import_status'], 'selected': sid == selected_id,
            'comparable': snap['import_status'] == 'ready',
            'retrieved_at': snap['retrieved_at'],
            'retrieval_note': 'When this local copy was downloaded; not a publication or service date.',
            'archive': archive,
            'feed_info': {'present': has_info if present else None,
                          'publisher_name': info.get('feed_publisher_name') or None,
                          'feed_version': info.get('feed_version') or None,
                          'version_label_date': version_date(info.get('feed_version')),
                          'declared_start': _iso(info.get('feed_start_date')),
                          'declared_end': _iso(info.get('feed_end_date'))},
            'observed_coverage': {'start': snap['feed_start'], 'end': snap['feed_end']},
            'validation': ({k: record[k] for k in ('validator_version', 'validation_date', 'errors', 'warnings', 'infos')}
                           if record else None),
            'validation_note': ('Local MobilityData report for this exact archive (SHA-256 match).' if record
                                else 'No validation report matches this archive hash.'),
        }
        if service_day is not None:
            item['date_in_coverage'] = _covered(snap, service_day)
        if snap['import_status'] == 'ready':
            item['counts'] = self._counts(sid)
        return item

    # -- per-snapshot facts ----------------------------------------------------------
    def _facts(self, sid: str) -> dict:
        with self._lock:
            if sid in self._static:
                return self._static[sid]
        con = self.con
        routes = {r['route_id']: r for r in rows(con, '''SELECT route_id,agency_id,short_name,long_name,route_type,
            route_color FROM routes WHERE snapshot_id=?''', (sid,))}
        stops = {s['stop_id']: s for s in rows(con, '''SELECT stop_id,name,lat,lon,location_type,parent_station
            FROM stops WHERE snapshot_id=?''', (sid,))}
        trips = rows(con, 'SELECT trip_id,route_id,service_id,headsign FROM trips WHERE snapshot_id=?', (sid,))
        frequencies: dict[str, list[dict]] = defaultdict(list)
        for f in rows(con, 'SELECT trip_id,start_sec,end_sec,headway_secs,exact_times FROM frequencies WHERE snapshot_id=?', (sid,)):
            frequencies[f['trip_id']].append(f)
        # One grouped pass: which services call at each stop (indexed by snapshot_id).
        stop_services: dict[str, set[str]] = defaultdict(set)
        for r in rows(con, '''SELECT DISTINCT st.stop_id,t.service_id FROM stop_times st JOIN trips t
                ON t.snapshot_id=st.snapshot_id AND t.trip_id=st.trip_id WHERE st.snapshot_id=?''', (sid,)):
            stop_services[r['stop_id']].add(r['service_id'])
        headsigns: dict[str, set[str]] = defaultdict(set)
        for t in trips:
            if t['headsign']:
                headsigns[t['route_id']].add(t['headsign'])
        for route_id, route in routes.items():
            route['headsigns'] = sorted(headsigns.get(route_id, ()))
        shape_ids = one(con, 'SELECT COUNT(DISTINCT shape_id) AS n FROM shapes WHERE snapshot_id=?', (sid,))['n']
        facts = {'routes': routes, 'stops': stops, 'trips': trips, 'frequencies': dict(frequencies),
                 'stop_services': dict(stop_services), 'shape_ids': shape_ids,
                 'routes_with_trips': {t['route_id'] for t in trips}}
        with self._lock:
            self._static[sid] = facts
        return facts

    def _counts(self, sid: str) -> dict:
        f = self._facts(sid)
        return {'route_records': len(f['routes']), 'routes_with_trip_references': len(f['routes_with_trips'] & set(f['routes'])),
                'stop_records': len(f['stops']), 'stops_referenced_by_trips': len(set(f['stop_services']) & set(f['stops'])),
                'trip_templates': len(f['trips']), 'shape_ids': f['shape_ids']}

    def _shape_digests(self, sid: str) -> dict[str, str]:
        """Per-shape digest of the exact point sequence; computed only for comparisons."""
        with self._lock:
            if sid in self._shapes:
                return self._shapes[sid]
        try:
            # DuckDB: aggregate in SQL (1.3M ARST points in well under a second).
            result = {r['shape_id']: r['digest'] for r in rows(self.con, '''SELECT shape_id, md5(string_agg(
                CAST(point_sequence AS VARCHAR)||':'||CAST(lat AS VARCHAR)||':'||CAST(lon AS VARCHAR), ';'
                ORDER BY point_sequence)) AS digest FROM shapes WHERE snapshot_id=? GROUP BY shape_id''', (sid,))}
        except Exception:
            # SQLite (tests): same exact-sequence identity, computed in Python.
            digests: dict[str, hashlib._Hash] = {}
            for s in rows(self.con, 'SELECT shape_id,point_sequence,lat,lon FROM shapes WHERE snapshot_id=? ORDER BY shape_id,point_sequence', (sid,)):
                digests.setdefault(s['shape_id'], hashlib.sha256()).update(f"{s['point_sequence']}:{s['lat']!r}:{s['lon']!r};".encode())
            result = {k: v.hexdigest() for k, v in digests.items()}
        with self._lock:
            self._shapes[sid] = result
        return result

    def day_metrics(self, snap: dict, service_day: date) -> dict:
        """Schedule metrics for one GTFS service day; ``None`` values when out of coverage."""
        sid = snap['snapshot_id']
        f = self._facts(sid)
        if not _covered(snap, service_day):
            return {'service_date': service_day.isoformat(), 'available': False,
                    'reason': (f"{service_day.isoformat()} is outside this snapshot's calendar coverage "
                               f"({snap['feed_start']}–{snap['feed_end']}); its service is unknown, not zero."),
                    'routes_scheduled': None, 'stops_served': None, 'trip_templates': None,
                    'exact_trip_instances': None, 'frequency_only_templates': None, 'non_exact_windows': None,
                    'per_route': {}, 'served_stops': None}
        services = active_services(self.con, sid, service_day)
        per_route: dict[str, dict] = defaultdict(lambda: {'trip_templates': 0, 'exact_trip_instances': 0, 'non_exact_windows': 0})
        exact = frequency_only = windows = 0
        active = [t for t in f['trips'] if t['service_id'] in services]
        for t in active:
            freqs = f['frequencies'].get(t['trip_id'], [])
            n_exact = 1 if not freqs else sum(len(range(q['start_sec'], q['end_sec'], q['headway_secs']))
                                              for q in freqs if q['exact_times'] == 1)
            n_windows = sum(1 for q in freqs if q['exact_times'] != 1)
            if freqs and n_windows == len(freqs):
                frequency_only += 1
            exact += n_exact
            windows += n_windows
            r = per_route[t['route_id']]
            r['trip_templates'] += 1
            r['exact_trip_instances'] += n_exact
            r['non_exact_windows'] += n_windows
        served = {s for s, svc in f['stop_services'].items() if svc & services}
        return {'service_date': service_day.isoformat(), 'available': True, 'reason': None,
                'routes_scheduled': len(per_route), 'stops_served': len(served & set(f['stops'])),
                'trip_templates': len(active), 'exact_trip_instances': exact,
                'frequency_only_templates': frequency_only, 'non_exact_windows': windows,
                'per_route': dict(per_route), 'served_stops': served}

    # -- comparison -----------------------------------------------------------------
    def compare(self, source_id: str, baseline_id: str, comparison_id: str, *, policy: str,
                service_date: date | None = None, baseline_date: date | None = None,
                comparison_date: date | None = None, record_status: str = 'changed',
                record_limit: int = 100, record_offset: int = 0) -> dict:
        if one(self.con, 'SELECT source_id FROM feed_sources WHERE source_id=?', (source_id,)) is None:
            raise NotFound(f'Unknown source {source_id}')
        base, comp = self.snapshot(baseline_id), self.snapshot(comparison_id)
        for snap, role in ((base, 'baseline'), (comp, 'comparison')):
            if snap['source_id'] != source_id:
                raise HistoryError(f"The {role} snapshot belongs to source {snap['source_id']}, not {source_id}; "
                                   'snapshots of different operators are never compared.')
            if snap['import_status'] != 'ready':
                raise HistoryError(f"The {role} snapshot is not ready (status {snap['import_status']}).")
        if policy not in POLICIES:
            raise HistoryError(f'Unknown date policy {policy!r}; use same_date or different_dates.')
        if policy == 'same_date':
            if service_date is None or baseline_date or comparison_date:
                raise HistoryError('same_date requires date= and no baseline_date/comparison_date.')
            day_a = day_b = service_date
        else:
            if baseline_date is None or comparison_date is None or service_date is not None:
                raise HistoryError('different_dates requires baseline_date= and comparison_date= (and no date=).')
            day_a, day_b = baseline_date, comparison_date
        if record_status not in ('changed', 'all', *RECORD_STATUSES):
            raise HistoryError('record_status must be changed, all, added, removed, modified or unchanged.')
        key = (baseline_id, comparison_id, day_a.isoformat(), day_b.isoformat())
        with self._lock:
            full = self._compare_cache.get(key)
        if full is None:
            full = self._build(base, comp, day_a, day_b, policy)
            with self._lock:
                self._compare_cache[key] = full
                if len(self._compare_cache) > 64:
                    self._compare_cache.pop(next(iter(self._compare_cache)))
        return _page(full, record_status, record_limit, record_offset)

    def _build(self, base: dict, comp: dict, day_a: date, day_b: date, policy: str) -> dict:
        self.compare_builds += 1
        fa, fb = self._facts(base['snapshot_id']), self._facts(comp['snapshot_id'])
        ma, mb = self.day_metrics(base, day_a), self.day_metrics(comp, day_b)
        same_snapshot = base['snapshot_id'] == comp['snapshot_id']
        attributable = policy == 'same_date' and ma['available'] and mb['available'] and not same_snapshot
        notices = []
        if same_snapshot:
            notices.append({'code': 'same_snapshot', 'level': 'caution',
                            'text': 'Both sides are the same feed version. Any difference comes only from the service dates.'})
        if policy == 'different_dates':
            notices.append({'code': 'different_dates', 'level': 'caution',
                            'text': (f'Both the feed version and the service date differ ({day_a} vs {day_b}). Differences are '
                                     'not attributable to the feed update alone, and this is not a timetable-revision comparison.')})
        for metrics, snap, role in ((ma, base, 'baseline'), (mb, comp, 'comparison')):
            if not metrics['available']:
                notices.append({'code': f'{role}_date_outside_coverage', 'level': 'caution', 'text': metrics['reason']})
        if policy == 'same_date' and not (ma['available'] and mb['available']):
            notices.append({'code': 'not_comparable_on_date', 'level': 'caution',
                            'text': 'The date is not covered by both snapshots, so schedule metrics are not comparable. '
                                    'Choose a date inside both calendar coverages, or the different_dates policy.'})
        static_a, static_b = self._counts(base['snapshot_id']), self._counts(comp['snapshot_id'])
        summary = {k: _delta(static_a[k], static_b[k], attributable=not same_snapshot)
                   for k in ('route_records', 'routes_with_trip_references', 'stop_records', 'stops_referenced_by_trips',
                             'trip_templates', 'shape_ids')}
        for k in ('routes_scheduled', 'stops_served', 'trip_templates', 'exact_trip_instances',
                  'frequency_only_templates', 'non_exact_windows'):
            summary[f'{k}_on_date' if k == 'trip_templates' else k] = _delta(ma[k], mb[k], attributable=attributable)
        routes = self._route_records(fa, fb, ma, mb, attributable)
        stops = self._stop_records(fa, fb, ma, mb)
        shapes_a, shapes_b = self._shape_digests(base['snapshot_id']), self._shape_digests(comp['snapshot_id'])
        shape_counts = Counter()
        for shape_id in set(shapes_a) | set(shapes_b):
            shape_counts[('added' if shape_id not in shapes_a else 'removed' if shape_id not in shapes_b
                          else 'unchanged' if shapes_a[shape_id] == shapes_b[shape_id] else 'modified')] += 1
        return {
            'source_id': base['source_id'], 'policy': policy,
            'baseline': _side(base, day_a, ma), 'comparison': _side(comp, day_b, mb),
            'synthetic': self._synthetic(base) or self._synthetic(comp),
            'notices': notices, 'identity_note': IDENTITY_NOTE,
            'definitions': DEFINITIONS, 'summary': summary,
            'routes': routes, 'stops': stops,
            'shapes': {'counts': {s: shape_counts.get(s, 0) for s in RECORD_STATUSES},
                       'note': ('Shape IDs compared by their exact point sequences. A changed or new shape ID is a '
                                'source-geometry record difference, not a verified change of physical path.')},
        }

    def _synthetic(self, snap) -> bool:
        """Fixture feeds are named SYNTHETIC by their agency; never present them as observed history."""
        return snap['source_id'].startswith('synthetic') or any(
            (a['name'] or '').upper().startswith('SYNTHETIC')
            for a in rows(self.con, 'SELECT name FROM agencies WHERE snapshot_id=?', (snap['snapshot_id'],)))

    def _route_records(self, fa, fb, ma, mb, attributable):
        records = []
        for route_id in sorted(set(fa['routes']) | set(fb['routes'])):
            a, b = fa['routes'].get(route_id), fb['routes'].get(route_id)
            status, changes = _record_status(a, b, ROUTE_FIELDS)
            service = None
            if ma['available'] or mb['available']:
                sa = ma['per_route'].get(route_id, {}).get('trip_templates', 0) if ma['available'] and a else (0 if ma['available'] else None)
                sb = mb['per_route'].get(route_id, {}).get('trip_templates', 0) if mb['available'] and b else (0 if mb['available'] else None)
                service = _delta(sa, sb, attributable=attributable)
                service['metric'] = 'trip_templates_on_date'
            records.append({'id': route_id, 'status': status, 'changes': changes,
                            'baseline': _route_view(a), 'comparison': _route_view(b), 'service': service})
        return records

    def _stop_records(self, fa, fb, ma, mb):
        records = []
        for stop_id in sorted(set(fa['stops']) | set(fb['stops'])):
            a, b = fa['stops'].get(stop_id), fb['stops'].get(stop_id)
            status, changes = _record_status(a, b, STOP_FIELDS)
            moved = None
            if a and b and None not in (a['lat'], a['lon'], b['lat'], b['lon']):
                moved = round(distance_m(a['lat'], a['lon'], b['lat'], b['lon']), 1)
                if moved > COORDINATE_TOLERANCE_M:
                    changes.append({'field': 'coordinates', 'baseline': [a['lat'], a['lon']],
                                    'comparison': [b['lat'], b['lon']], 'distance_m': moved})
                    status = 'modified'
            elif a and b and (a['lat'] is None) != (b['lat'] is None):
                changes.append({'field': 'coordinates', 'baseline': [a['lat'], a['lon']], 'comparison': [b['lat'], b['lon']]})
                status = 'modified'
            served = {'baseline': (stop_id in ma['served_stops']) if ma['available'] and a else None,
                      'comparison': (stop_id in mb['served_stops']) if mb['available'] and b else None}
            records.append({'id': stop_id, 'status': status, 'changes': changes, 'moved_m': moved,
                            'baseline': _stop_view(a), 'comparison': _stop_view(b), 'served_on_date': served})
        return records


DEFINITIONS = {
    'route_records': 'Rows in routes.txt of the snapshot.',
    'routes_with_trip_references': 'Route records referenced by at least one trip.',
    'routes_scheduled': 'Routes with at least one trip whose service is active on the service day (calendar + calendar_dates).',
    'stop_records': 'Rows in stops.txt of the snapshot (records, not verified physical stops).',
    'stops_referenced_by_trips': 'Stop records referenced by any stop_times row.',
    'stops_served': 'Stop records called at by a trip active on the service day.',
    'trip_templates': 'Rows in trips.txt (templates, not departures).',
    'trip_templates_on_date': 'Trip templates whose service is active on the service day.',
    'exact_trip_instances': 'Exactly timed trip runs on the service day: one per non-frequency template, plus every '
                            'departure of exact_times=1 frequencies.',
    'frequency_only_templates': 'Active templates whose only departures are exact_times=0 headway windows.',
    'non_exact_windows': 'Active exact_times=0 frequency windows (headways without exact departure times).',
    'shape_ids': 'Distinct shape_id values in shapes.txt.',
    'percent_change': '100 × (comparison − baseline) / baseline; undefined (null) when the baseline is zero.',
    'status': 'unchanged / changed for metrics; incomparable when a side is outside its calendar coverage. '
              'Records: added / removed / modified / unchanged by ID within this source.',
}


def _record_status(a, b, fields):
    if a is None:
        return 'added', []
    if b is None:
        return 'removed', []
    changes = [{'field': f, 'baseline': a.get(f), 'comparison': b.get(f)} for f in fields if a.get(f) != b.get(f)]
    return ('modified' if changes else 'unchanged'), changes


def _route_view(r):
    return None if r is None else {k: r.get(k) for k in ('short_name', 'long_name', 'route_type', 'agency_id', 'route_color', 'headsigns')}


def _stop_view(s):
    return None if s is None else {k: s.get(k) for k in ('name', 'lat', 'lon', 'location_type', 'parent_station')}


def _side(snap, day, metrics):
    return {'snapshot_id': snap['snapshot_id'], 'content_sha256': snap['content_sha256'],
            'retrieved_at': snap['retrieved_at'], 'observed_coverage': {'start': snap['feed_start'], 'end': snap['feed_end']},
            'service_date': day.isoformat(), 'date_in_coverage': metrics['available'], 'reason': metrics['reason']}


def _page(full: dict, status: str, limit: int, offset: int) -> dict:
    out = {k: v for k, v in full.items() if k not in ('routes', 'stops')}
    for kind in ('routes', 'stops'):
        records = full[kind]
        counts = Counter(r['status'] for r in records)
        chosen = [r for r in records if status == 'all' or r['status'] == status
                  or (status == 'changed' and r['status'] != 'unchanged')]
        page = chosen[offset:offset+limit]
        out[kind] = {'counts': {s: counts.get(s, 0) for s in RECORD_STATUSES}, 'total': len(records),
                     'status_filter': status, 'matching': len(chosen), 'offset': offset, 'limit': limit,
                     'records': page, 'truncated': offset+limit < len(chosen),
                     'next_offset': offset+limit if offset+limit < len(chosen) else None}
    return out


def _covered(snap: dict, day: date) -> bool:
    return bool(snap.get('feed_start')) and snap['feed_start'] <= day.isoformat() <= (snap.get('feed_end') or '')


def _iso(raw):
    if not raw or len(raw) != 8 or not raw.isdigit():
        return None
    try:
        return date(int(raw[:4]), int(raw[4:6]), int(raw[6:])).isoformat()
    except ValueError:
        return None


@lru_cache(maxsize=64)
def _file_sha256(path: str, size: int, mtime_ns: int) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()
