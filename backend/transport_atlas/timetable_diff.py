"""Phase 8A: per-trip timetable differences between two snapshots of ONE source on ONE service day.

Read-only. Operating trips come from ``schedule.active_services`` (calendar weekday and
date range, then calendar_dates additions/removals take precedence), the same service-day
semantics the rest of History uses. Times are GTFS service-day seconds and are shown as
GTFS clock text, so 25:10:00 stays 25:10:00.

What counts as a timetable change: the ordered stop IDs and each stop's arrival and
departure seconds. ``shape_id`` (geometry) is never part of it; a trip whose only
difference is its shape ID is ``unchanged`` and flagged ``shape_id_changed``.

Matching (transparent and conservative; never forced):

1. ``same_trip_id``: the same GTFS trip ID in both snapshots of the source.
2. ``identical_timetable_new_id``: among the rest, a pair with exactly the same route,
   direction and stop/time sequence, if that sequence occurs exactly once on each side
   (a renumbered trip ID).
3. ``unique_similar_trip``: among the rest, same route and direction, first departure
   within ``MAX_SHIFT_SECONDS`` and stop-sequence similarity at least ``MIN_SIMILARITY``.
   A pair is matched only when each trip is the other's single best candidate and the
   best is strictly better than the runner-up; otherwise every trip involved is
   ``ambiguous`` and its candidates are listed.

Everything else is ``added`` or ``removed``: a published-record change, which may be a
renumbering or seasonal revision the rules above could not prove, never evidence of a
real-world closure. Trips driven by ``frequencies`` have no single exact timetable and
are ``unsupported``.
"""
from __future__ import annotations

from collections import defaultdict
from difflib import SequenceMatcher

from .schedule import active_services
from .storage import rows

MAX_SHIFT_SECONDS = 30 * 60
MIN_SIMILARITY = 0.6
MAX_TRIPS_PER_SIDE = 30000
STATUSES = ('changed', 'added', 'removed', 'ambiguous', 'unsupported', 'unchanged')
FILTERS = ('differences', 'all') + STATUSES
NOTE = ('A difference between two archived GTFS versions is a change in the PUBLISHED timetable records for '
        'this service date. It is not proof that service changed on the road, nor of when the publisher made '
        'the change. Trip IDs are matched only within this one source; a new or missing trip ID can be a '
        'renumbering or a seasonal revision.')
MATCHING = {
    'same_trip_id': 'Same GTFS trip ID in both versions of this source.',
    'identical_timetable_new_id': ('Different trip ID, but the same route, direction, stops and times, occurring '
                                   'exactly once in each version.'),
    'unique_similar_trip': (f'Different trip ID; same route and direction, first departure within '
                            f'{MAX_SHIFT_SECONDS // 60} min and stop-sequence similarity >= {MIN_SIMILARITY}; each '
                            'trip is the other\'s single best candidate, with no tie.'),
}


def gtfs_time(seconds: int | None) -> str | None:
    """GTFS service-day clock text; hours may exceed 24 (e.g. 25:10:00)."""
    if seconds is None:
        return None
    return f'{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}'


def operating_trips(con, snapshot_id: str, service_day) -> dict[str, dict]:
    """Trips operating on the service day, with their ordered stop times (snapshot-scoped reads)."""
    services = sorted(active_services(con, snapshot_id, service_day))
    if not services:
        return {}
    marks = ','.join('?' * len(services))
    trips = {t['trip_id']: {**t, 'stops': [], 'frequencies': []} for t in rows(
        con, f'''SELECT trip_id,route_id,service_id,shape_id,direction_id,headsign FROM trips
                 WHERE snapshot_id=? AND service_id IN ({marks})''', (snapshot_id, *services))}
    if len(trips) > MAX_TRIPS_PER_SIDE:
        raise OverflowError(len(trips))
    for r in rows(con, f'''SELECT st.trip_id,st.stop_sequence,st.stop_id,st.arrival_sec,st.departure_sec
            FROM stop_times st JOIN trips t ON t.snapshot_id=st.snapshot_id AND t.trip_id=st.trip_id
            WHERE st.snapshot_id=? AND t.service_id IN ({marks}) ORDER BY st.trip_id,st.stop_sequence''',
                      (snapshot_id, *services)):
        trips[r['trip_id']]['stops'].append((r['stop_sequence'], r['stop_id'], r['arrival_sec'], r['departure_sec']))
    for f in rows(con, f'''SELECT f.trip_id,f.start_sec,f.end_sec,f.headway_secs,f.exact_times FROM frequencies f
            JOIN trips t ON t.snapshot_id=f.snapshot_id AND t.trip_id=f.trip_id
            WHERE f.snapshot_id=? AND t.service_id IN ({marks})''', (snapshot_id, *services)):
        trips[f['trip_id']]['frequencies'].append(f)
    return trips


def _first_departure(t):
    s = t['stops']
    return None if not s else (s[0][3] if s[0][3] is not None else s[0][2])


def _last_arrival(t):
    s = t['stops']
    return None if not s else (s[-1][2] if s[-1][2] is not None else s[-1][3])


def _signature(t):
    return tuple((stop_id, arr, dep) for _, stop_id, arr, dep in t['stops'])


def _usable(t):
    return bool(t['stops']) and not t['frequencies'] and _first_departure(t) is not None


def _view(t):
    return None if t is None else {
        'trip_id': t['trip_id'], 'route_id': t['route_id'], 'direction_id': t['direction_id'],
        'headsign': t['headsign'], 'service_id': t['service_id'], 'shape_id': t['shape_id'],
        'first_departure': gtfs_time(_first_departure(t)), 'last_arrival': gtfs_time(_last_arrival(t)),
        'stop_count': len(t['stops'])}


def _similarity(a, b) -> float:
    return SequenceMatcher(None, [s[1] for s in a['stops']], [s[1] for s in b['stops']], autojunk=False).ratio()


def stop_differences(a, b) -> list[dict]:
    """Stop-by-stop differences, aligned on the ordered stop-ID sequences."""
    sa, sb = a['stops'], b['stops']
    out = []
    matcher = SequenceMatcher(None, [s[1] for s in sa], [s[1] for s in sb], autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == 'equal':
            for x, y in zip(sa[i1:i2], sb[j1:j2]):
                if (x[2], x[3]) != (y[2], y[3]):
                    out.append({'change': 'time', 'stop_id': x[1], 'baseline_sequence': x[0], 'comparison_sequence': y[0],
                                'baseline_arrival': gtfs_time(x[2]), 'comparison_arrival': gtfs_time(y[2]),
                                'baseline_departure': gtfs_time(x[3]), 'comparison_departure': gtfs_time(y[3]),
                                'shift_seconds': None if x[3] is None or y[3] is None else y[3] - x[3]})
            continue
        for x in sa[i1:i2]:
            out.append({'change': 'removed_stop', 'stop_id': x[1], 'baseline_sequence': x[0],
                        'baseline_arrival': gtfs_time(x[2]), 'baseline_departure': gtfs_time(x[3])})
        for y in sb[j1:j2]:
            out.append({'change': 'added_stop', 'stop_id': y[1], 'comparison_sequence': y[0],
                        'comparison_arrival': gtfs_time(y[2]), 'comparison_departure': gtfs_time(y[3])})
    return out


def _change_kinds(a, b) -> list[str]:
    kinds = []
    if _first_departure(a) != _first_departure(b):
        kinds.append('departure_time')
    if _last_arrival(a) != _last_arrival(b):
        kinds.append('last_arrival')
    if len(a['stops']) != len(b['stops']):
        kinds.append('stop_count')
    ids_a, ids_b = [s[1] for s in a['stops']], [s[1] for s in b['stops']]
    if ids_a != ids_b:
        kinds.append('stop_pattern')
    elif [(s[2], s[3]) for s in a['stops']] != [(s[2], s[3]) for s in b['stops']]:
        kinds.append('stop_times')
    for field, kind in (('route_id', 'route'), ('direction_id', 'direction'), ('headsign', 'headsign')):
        if (a[field] or None) != (b[field] or None):
            kinds.append(kind)
    return kinds


def _summary(kinds, a, b) -> str:
    parts = []
    if 'departure_time' in kinds:
        shift = _first_departure(b) - _first_departure(a)
        parts.append(f'departs {gtfs_time(_first_departure(a))} → {gtfs_time(_first_departure(b))} '
                     f'({"+" if shift > 0 else "−"}{abs(shift) // 60} min{"" if abs(shift) % 60 == 0 else " " + str(abs(shift) % 60) + " s"})')
    if 'stop_count' in kinds:
        parts.append(f'{len(a["stops"])} → {len(b["stops"])} stops')
    elif 'stop_pattern' in kinds:
        parts.append('different stops (same count)')
    if 'last_arrival' in kinds:
        parts.append(f'last arrival {gtfs_time(_last_arrival(a))} → {gtfs_time(_last_arrival(b))}')
    if 'stop_times' in kinds and 'departure_time' not in kinds and 'last_arrival' not in kinds:
        parts.append('intermediate stop times changed')
    for kind in ('route', 'direction', 'headsign'):
        if kind in kinds:
            parts.append(f'{kind} changed')
    return '; '.join(parts) or 'timetable unchanged'


def _record(status, a, b, basis=None, candidates=None, note=None):
    rec = {'status': status, 'baseline': _view(a), 'comparison': _view(b),
           'route_id': (b or a)['route_id'], 'departure': gtfs_time(_first_departure(a or b)),
           'match': basis, 'change_kinds': [], 'summary': None, 'shape_id_changed': False}
    if a is not None and b is not None:
        rec['shape_id_changed'] = (a['shape_id'] or None) != (b['shape_id'] or None)
        if status in ('changed', 'unchanged'):
            rec['change_kinds'] = _change_kinds(a, b)
            rec['summary'] = _summary(rec['change_kinds'], a, b)
    if candidates is not None:
        rec['candidates'] = candidates
    if note:
        rec['note'] = note
    key_trip = (a or b)['trip_id']
    rec['id'] = f"{a['trip_id'] if a else ''}→{b['trip_id'] if b else ''}"
    rec['_order'] = (rec['route_id'] or '', _first_departure(a or b) if _first_departure(a or b) is not None else 10**9,
                     key_trip)
    return rec


def diff_trips(old: dict[str, dict], new: dict[str, dict]) -> dict:
    """Pure matching and classification; ``old``/``new`` come from ``operating_trips``."""
    records, used_old, used_new = [], set(), set()
    stages = defaultdict(int)

    def classify(a, b, basis):
        used_old.add(a['trip_id'])
        used_new.add(b['trip_id'])
        stages[basis['stage']] += 1
        if not (_usable(a) and _usable(b)):
            records.append(_record('unsupported', a, b, basis, note='frequency-based or without usable times'))
            return
        same = _signature(a) == _signature(b) and not {'route', 'direction', 'headsign'} & set(_change_kinds(a, b))
        records.append(_record('unchanged' if same else 'changed', a, b, basis))

    # Stage 1: same trip ID.
    for trip_id in sorted(set(old) & set(new)):
        classify(old[trip_id], new[trip_id], {'stage': 'same_trip_id', 'confidence': 'exact_id'})
    # Unusable leftovers (frequency-based, no times) are never matched across IDs.
    for side, pool, used in (('baseline', old, used_old), ('comparison', new, used_new)):
        for trip_id in sorted(set(pool) - used):
            t = pool[trip_id]
            if not _usable(t):
                used.add(trip_id)
                records.append(_record('unsupported', t if side == 'baseline' else None,
                                       t if side == 'comparison' else None,
                                       note='frequency-based or without usable times; not matched across trip IDs'))

    def key(t):
        return (t['route_id'], t['direction_id'])

    # Stage 2: identical timetable under a new ID, unique on both sides.
    by_sig_old, by_sig_new = defaultdict(list), defaultdict(list)
    for t in old.values():
        if t['trip_id'] not in used_old:
            by_sig_old[(key(t), _signature(t))].append(t)
    for t in new.values():
        if t['trip_id'] not in used_new:
            by_sig_new[(key(t), _signature(t))].append(t)
    for sig in sorted(set(by_sig_old) & set(by_sig_new), key=lambda s: (str(s[0]), str(s[1][:1]))):
        a_list, b_list = by_sig_old[sig], by_sig_new[sig]
        if len(a_list) == 1 and len(b_list) == 1:
            classify(a_list[0], b_list[0], {'stage': 'identical_timetable_new_id', 'confidence': 'identical_timetable'})

    # Stage 3: unique, mutually best, similar trip (route + direction + time window + stop similarity).
    rest_old = [old[k] for k in sorted(set(old) - used_old)]
    rest_new = [new[k] for k in sorted(set(new) - used_new)]
    cand_old, cand_new = defaultdict(list), defaultdict(list)
    for a in rest_old:
        for b in rest_new:
            if key(a) != key(b):
                continue
            shift = _first_departure(b) - _first_departure(a)
            if abs(shift) > MAX_SHIFT_SECONDS:
                continue
            sim = _similarity(a, b)
            if sim < MIN_SIMILARITY:
                continue
            c = {'baseline_trip_id': a['trip_id'], 'comparison_trip_id': b['trip_id'], 'departure_shift_seconds': shift,
                 'stop_sequence_similarity': round(sim, 3)}
            rank = (abs(shift), -round(sim, 6))
            cand_old[a['trip_id']].append((rank, b['trip_id'], c))
            cand_new[b['trip_id']].append((rank, a['trip_id'], c))

    def best(cands):
        ordered = sorted(cands, key=lambda x: (x[0], x[1]))
        if not ordered:
            return None, False
        unique = len(ordered) == 1 or ordered[0][0] < ordered[1][0]
        return ordered[0], unique

    # Pairs: each trip is the other's single best candidate (no ties on either side).
    pairs = []
    for a in rest_old:
        top, unique = best(cand_old[a['trip_id']])
        if top is None or not unique:
            continue
        back, back_unique = best(cand_new[top[1]])
        if back_unique and back[1] == a['trip_id']:
            pairs.append((a, new[top[1]], top[2]))
    for a, b, c in pairs:
        classify(a, b, {'stage': 'unique_similar_trip', 'confidence': 'constrained_unique',
                        'departure_shift_seconds': c['departure_shift_seconds'],
                        'stop_sequence_similarity': c['stop_sequence_similarity'],
                        'route_id': a['route_id'], 'direction_id': a['direction_id']})
    # Left-overs that had candidates: a tie (or a non-mutual preference among still-free trips)
    # is "ambiguous"; a trip whose best candidate went to a strictly better pair stays unmatched.
    claimed_note = {}
    paired_old = {a['trip_id'] for a, _, _ in pairs}
    paired_new = {b['trip_id'] for _, b, _ in pairs}
    for side, pool, cands, used, other_used in (('baseline', old, cand_old, used_old, paired_new),
                                                ('comparison', new, cand_new, used_new, paired_old)):
        for trip_id in sorted(set(pool) - used):
            free = [c for c in cands[trip_id] if c[1] not in other_used]
            if not cands[trip_id]:
                continue
            if not free:
                claimed_note[(side, trip_id)] = ('its closest candidate(s) matched another trip more closely: '
                                                 + ', '.join(sorted(c[1] for c in cands[trip_id])))
                continue
            used.add(trip_id)
            t = pool[trip_id]
            records.append(_record('ambiguous', t if side == 'baseline' else None, t if side == 'comparison' else None,
                                   candidates=[c for _, _, c in sorted(free, key=lambda x: (x[0], x[1]))],
                                   note='more than one trip in the other version fits about equally well; not matched'))
    for trip_id in sorted(set(old) - used_old):
        records.append(_record('removed', old[trip_id], None,
                               note=claimed_note.get(('baseline', trip_id),
                                                     'no trip in the comparison version could be matched safely')))
    for trip_id in sorted(set(new) - used_new):
        records.append(_record('added', None, new[trip_id],
                               note=claimed_note.get(('comparison', trip_id),
                                                     'no trip in the baseline version could be matched safely')))
    records.sort(key=lambda r: r['_order'])
    counts = {s: sum(r['status'] == s for r in records) for s in STATUSES}
    old_accounted = sum(1 for r in records if r['baseline'] is not None)
    new_accounted = sum(1 for r in records if r['comparison'] is not None)
    return {'records': records, 'counts': counts, 'matched_by': dict(stages),
            'reconciliation': {'baseline_trips': len(old), 'baseline_accounted': old_accounted,
                               'comparison_trips': len(new), 'comparison_accounted': new_accounted,
                               'complete': old_accounted == len(old) and new_accounted == len(new)},
            'shape_id_only_changes': sum(1 for r in records if r['status'] == 'unchanged' and r['shape_id_changed'])}


def page(full: dict, *, status: str, route: str | None, limit: int, offset: int) -> dict:
    chosen = [r for r in full['records']
              if (status == 'all' or r['status'] == status or (status == 'differences' and r['status'] != 'unchanged'))
              and (route is None or r['route_id'] == route)]
    out = {k: v for k, v in full.items() if k != 'records'}
    out['records'] = [{k: v for k, v in r.items() if k != '_order'} for r in chosen[offset:offset + limit]]
    out['page'] = {'status_filter': status, 'route_filter': route, 'matching': len(chosen), 'offset': offset,
                   'limit': limit, 'truncated': offset + limit < len(chosen),
                   'next_offset': offset + limit if offset + limit < len(chosen) else None}
    out['routes'] = sorted({r['route_id'] for r in full['records'] if r['route_id'] is not None})
    return out


def single_trip(con, snapshot_id: str, trip_id: str) -> dict | None:
    """One trip's ordered stop times, read by its primary key (snapshot-scoped)."""
    found = rows(con, '''SELECT trip_id,route_id,service_id,shape_id,direction_id,headsign FROM trips
                         WHERE snapshot_id=? AND trip_id=?''', (snapshot_id, trip_id))
    if not found:
        return None
    trip = {**found[0], 'frequencies': rows(con, 'SELECT * FROM frequencies WHERE snapshot_id=? AND trip_id=?',
                                              (snapshot_id, trip_id))}
    trip['stops'] = [(r['stop_sequence'], r['stop_id'], r['arrival_sec'], r['departure_sec']) for r in rows(
        con, '''SELECT stop_sequence,stop_id,arrival_sec,departure_sec FROM stop_times
                WHERE snapshot_id=? AND trip_id=? ORDER BY stop_sequence''', (snapshot_id, trip_id))]
    return trip


def trip_detail(a: dict | None, b: dict | None, names_a: dict, names_b: dict) -> dict:
    def stops(t, names):
        return None if t is None else [{'sequence': s[0], 'stop_id': s[1], 'name': names.get(s[1]),
                                        'arrival': gtfs_time(s[2]), 'departure': gtfs_time(s[3])} for s in t['stops']]
    out = {'baseline': _view(a), 'comparison': _view(b), 'baseline_stops': stops(a, names_a),
           'comparison_stops': stops(b, names_b), 'differences': None,
           'note': 'Stop IDs are GTFS records of this source; the same ID is not proof of the same physical stop.'}
    if a is not None and b is not None:
        diffs = stop_differences(a, b)
        for d in diffs:
            d['name'] = names_b.get(d['stop_id']) or names_a.get(d['stop_id'])
        out['differences'] = diffs
    return out
