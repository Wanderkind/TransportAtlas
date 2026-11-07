"""Source-backed data-quality indicators. Read-only: never edits provider values.

Every indicator has the same shape so the route, stop and source inspectors can
present them consistently::

    {category, code, level, title, detail, evidence, source_id, snapshot_id}

``category`` is one of CATEGORIES. ``level`` is ``info`` (context) or
``caution`` (a reader should take it into account). No numerical reliability
score is produced: every indicator states its rule and its evidence.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
import json
from pathlib import Path
import re
from uuid import uuid4

from .gtfs import service_date
from .nearby import distance_m
from .provenance import declared_info, feed_info_present
from .schedule import active_services, WEEKDAYS
from .storage import one, rows

CATEGORIES = {
    'publisher': 'Publisher data limitation',
    'coverage': 'Missing or incomplete coverage',
    'timing': 'Suspicious scheduled times',
    'geometry': 'Schematic or uncertain geometry',
    'validation': 'Technical validation',
}

# MobilityData gtfs-validator v8.0.1 `fast_travel_between_consecutive_stops`
# thresholds (km/h) by GTFS route_type; other types use the validator default.
SPEED_LIMIT_KPH = {0: 100, 1: 150, 2: 500, 3: 150, 4: 80, 5: 30, 6: 50, 7: 50, 11: 150, 12: 150}
DEFAULT_SPEED_LIMIT_KPH = 200
# Publishers round times to these steps; a pair's step is the largest dividing both.
TIME_STEPS = (60, 30, 15, 10, 5, 1)
# A <= 1 s interval at second precision between stops this far apart.
NEAR_ZERO_MIN_M = 50
# Bump when the interval rules change, invalidating cached feed summaries.
TIMING_RULES = 'lower-bound-speed+near-zero/v1'
LARGE_STOP_LIST = 300

# A selected date this long after a publisher-dated version, with this share of
# the busiest same-weekday volume or less, gets a contextual (not alarming) note.
PROJECTION_DAYS = 90
REDUCED_SHARE = 0.75


def indicator(category: str, code: str, title: str, detail: str, *, level: str = 'info',
              evidence: dict | None = None, source_id: str | None = None, snapshot_id: str | None = None) -> dict:
    assert category in CATEGORIES and level in ('info', 'caution')
    return {'category': category, 'category_label': CATEGORIES[category], 'code': code, 'level': level,
            'title': title, 'detail': detail, 'evidence': evidence or {},
            'source_id': source_id, 'snapshot_id': snapshot_id}


def clock(seconds: int | None) -> str | None:
    """GTFS clock text; hours may exceed 24 for service-day overnight times."""
    if seconds is None:
        return None
    sign = '-' if seconds < 0 else ''
    seconds = abs(seconds)
    return f'{sign}{seconds//3600:02d}:{(seconds//60) % 60:02d}:{seconds % 60:02d}'


# ---------------------------------------------------------------------------
# Stop-to-stop intervals (reusable for travel-time analysis)
# ---------------------------------------------------------------------------

def speed_limit_kph(route_type: int | None) -> int:
    return SPEED_LIMIT_KPH.get(route_type, DEFAULT_SPEED_LIMIT_KPH)


def segment_intervals(stops: list[dict], route_type: int | None) -> dict:
    """Classify each consecutive stop pair of one trip (rows in stop_sequence order).

    Times are GTFS service-day seconds as imported, so 24:01:00 follows 23:59:00
    without any midnight wrap. Provider values are reported, never changed.

    * ``elapsed`` = next arrival (else departure) minus previous departure
      (else arrival). Untimed pairs are counted, not guessed.
    * Timing resolution: the largest of 60/30/15/10/5/1 s dividing both times.
      Rounded times can understate the true gap by up to that step, so the
      step is added to the elapsed time (e.g. ARST publishes half minutes).
    * Distance is the straight-line (haversine) distance between stop records.
      The vehicle's path is at least that long, so ``min_implied_speed_kph`` is
      a lower bound on the speed the published times imply.
    * ``implausible``: that lower bound exceeds the mode threshold. Suspicious,
      not proven wrong: coordinates or times could be at fault.
    * ``implausible`` also: elapsed <= 1 s at 1 s resolution between stops at
      least 50 m apart (placeholder-like spacing, as in ASPO's feed).
    * ``inconsistent``: time decreases between stops, or departure precedes
      arrival at a stop. This breaks the GTFS ordering rule, so the published
      values are definitely inconsistent with each other.
    """
    limit = speed_limit_kph(route_type)
    findings, checked, untimed, no_distance = [], 0, 0, 0
    for stop in stops:
        if stop.get('arrival_sec') is not None and stop.get('departure_sec') is not None \
                and stop['departure_sec'] < stop['arrival_sec']:
            findings.append({'classification': 'inconsistent', 'reason': 'departure_before_arrival',
                             'from_sequence': stop['stop_sequence'], 'to_sequence': stop['stop_sequence'],
                             'from_stop_id': stop['stop_id'], 'to_stop_id': stop['stop_id'],
                             'from_time': clock(stop['arrival_sec']), 'to_time': clock(stop['departure_sec']),
                             'elapsed_seconds': stop['departure_sec']-stop['arrival_sec']})
    for a, b in zip(stops, stops[1:]):
        start = a['departure_sec'] if a.get('departure_sec') is not None else a.get('arrival_sec')
        end = b['arrival_sec'] if b.get('arrival_sec') is not None else b.get('departure_sec')
        if start is None or end is None:
            untimed += 1
            continue
        checked += 1
        elapsed = end-start
        base = {'from_sequence': a['stop_sequence'], 'to_sequence': b['stop_sequence'],
                'from_stop_id': a['stop_id'], 'to_stop_id': b['stop_id'],
                'from_stop_name': a.get('name'), 'to_stop_name': b.get('name'),
                'from_time': clock(start), 'to_time': clock(end), 'elapsed_seconds': elapsed}
        if elapsed < 0:
            findings.append({**base, 'classification': 'inconsistent', 'reason': 'time_decreases'})
            continue
        if None in (a.get('lat'), a.get('lon'), b.get('lat'), b.get('lon')):
            no_distance += 1
            continue
        metres = distance_m(a['lat'], a['lon'], b['lat'], b['lon'])
        resolution = next(step for step in TIME_STEPS if start % step == 0 and end % step == 0)
        speed = metres/(elapsed+resolution)*3.6
        reason = ('min_implied_speed_exceeds_threshold' if speed > limit else
                  'near_zero_interval' if resolution == 1 and elapsed <= 1 and metres >= NEAR_ZERO_MIN_M else None)
        if reason:
            findings.append({**base, 'classification': 'implausible', 'reason': reason,
                             'straight_line_m': round(metres, 1), 'timing_resolution_seconds': resolution,
                             'min_implied_speed_kph': round(speed, 1), 'threshold_kph': limit})
    return {'segments_checked': checked, 'segments_untimed': untimed, 'segments_without_coordinates': no_distance,
            'threshold_kph': limit, 'distance_basis': 'straight_line', 'findings': findings}


def _trip_rows(con, snapshot_id: str, where: str, params: tuple) -> dict[str, list[dict]]:
    by_trip: dict[str, list[dict]] = defaultdict(list)
    for row in rows(con, f'''SELECT st.trip_id,st.stop_sequence,st.stop_id,st.arrival_sec,st.departure_sec,
            s.name,s.lat,s.lon FROM stop_times st JOIN trips t ON t.snapshot_id=st.snapshot_id AND t.trip_id=st.trip_id
            LEFT JOIN stops s ON s.snapshot_id=st.snapshot_id AND s.stop_id=st.stop_id
            WHERE st.snapshot_id=? AND {where} ORDER BY st.trip_id,st.stop_sequence''', (snapshot_id, *params)):
        by_trip[row['trip_id']].append(row)
    return by_trip


def timing_summary(con, snapshot_id: str, route_id: str | None = None) -> dict:
    """Trips (templates) with flagged intervals, for one route or a whole feed."""
    route_types = {r['route_id']: r['route_type'] for r in rows(con, 'SELECT route_id,route_type FROM routes WHERE snapshot_id=?', (snapshot_id,))}
    trip_routes = {t['trip_id']: t['route_id'] for t in rows(
        con, 'SELECT trip_id,route_id FROM trips WHERE snapshot_id=?' + (' AND route_id=?' if route_id else ''),
        (snapshot_id, route_id) if route_id else (snapshot_id,))}
    by_trip = _trip_rows(con, snapshot_id, 't.route_id=?' if route_id else '1=1', (route_id,) if route_id else ())
    counts = Counter()
    implausible_trips, inconsistent_trips, examples, routes_affected = set(), set(), [], Counter()
    for trip_id, stops in by_trip.items():
        result = segment_intervals(stops, route_types.get(trip_routes.get(trip_id)))
        counts['segments_checked'] += result['segments_checked']
        counts['segments_untimed'] += result['segments_untimed']
        for finding in result['findings']:
            counts[finding['classification']] += 1
            (implausible_trips if finding['classification'] == 'implausible' else inconsistent_trips).add(trip_id)
            if len(examples) < 3:
                examples.append({'trip_id': trip_id, **finding})
        if result['findings']:
            routes_affected[trip_routes.get(trip_id)] += 1
    return {'trip_templates_checked': len(by_trip), 'segments_checked': counts['segments_checked'],
            'segments_untimed': counts['segments_untimed'],
            'implausible_segments': counts['implausible'], 'inconsistent_segments': counts['inconsistent'],
            'trip_templates_with_implausible': len(implausible_trips),
            'trip_templates_with_inconsistent': len(inconsistent_trips),
            'routes_affected': len(routes_affected), 'examples': examples, 'distance_basis': 'straight_line'}


def cached_feed_timing(con, snapshot: dict, cache_folder: Path | None) -> dict:
    """Feed-wide summary, memoized per immutable snapshot (memory, then disk)."""
    sid = snapshot['snapshot_id']
    if sid in _TIMING_CACHE:
        return _TIMING_CACHE[sid]
    path = Path(cache_folder)/f'{sid}-timing.json' if cache_folder else None
    if path is not None:
        try:
            stored = json.loads(path.read_text(encoding='utf-8'))
            if stored.get('rules') == TIMING_RULES and stored.get('content_sha256') == snapshot['content_sha256']:
                _TIMING_CACHE[sid] = stored['summary']
                return stored['summary']
        except (OSError, ValueError, AttributeError):
            pass
    summary = timing_summary(con, sid)
    _TIMING_CACHE[sid] = summary
    if path is not None:
        temporary = path.with_name(f'{path.name}.{uuid4().hex}.part')
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary.write_text(json.dumps({'rules': TIMING_RULES, 'content_sha256': snapshot['content_sha256'],
                                             'summary': summary}), encoding='utf-8')
            temporary.replace(path)
        except OSError:
            pass
        finally:
            temporary.unlink(missing_ok=True)
    return summary


def timing_indicators(summary: dict, *, scope: str, source_id=None, snapshot_id=None) -> list[dict]:
    out = []
    if summary['implausible_segments']:
        out.append(indicator('timing', 'implausible_stop_intervals',
            f"Implausibly short stop-to-stop times in {summary['trip_templates_with_implausible']} of "
            f"{summary['trip_templates_checked']} trip templates on this {scope}",
            f"{summary['implausible_segments']} consecutive-stop intervals are too short to be plausible: they imply a "
            'speed above the mode threshold even over the straight-line distance (the shortest possible path), or '
            'are 1 second or less between stops at least 50 m apart. This suggests placeholder times; it is not '
            'proof of an error. Times are shown exactly as published. Other trips are not affected '
            'by this finding.', level='caution', source_id=source_id, snapshot_id=snapshot_id,
            evidence={k: summary[k] for k in ('implausible_segments', 'segments_checked', 'examples')}))
    if summary['inconsistent_segments']:
        out.append(indicator('timing', 'inconsistent_stop_times',
            f"Published times decrease within {summary['trip_templates_with_inconsistent']} trip templates",
            'A later stop is timed before an earlier one (or departs before it arrives). These published values '
            'contradict each other under the GTFS ordering rule; they are shown unchanged.', level='caution',
            source_id=source_id, snapshot_id=snapshot_id,
            evidence={'inconsistent_segments': summary['inconsistent_segments']}))
    return out


# ---------------------------------------------------------------------------
# Stop service status
# ---------------------------------------------------------------------------

STATUS_LABELS = {
    'scheduled': 'Scheduled service on this date',
    'not_scheduled_on_date': 'Trips call here, but none on this date',
    'no_trip_references': 'No trips reference this stop record',
    'unknown': 'Service status cannot be established',
}


def stop_service_status(con, snapshot: dict, stop_ids: list[str], service_day: date) -> dict[str, dict]:
    """Classify stops of one snapshot for a GTFS service day.

    Counts are separated by precision: ``trip_templates`` (trips whose pattern
    calls here), ``exact_calls_on_date`` (exactly timed calls, expanding
    ``exact_times=1`` frequencies) and ``frequency_templates_on_date``
    (``exact_times=0`` trips whose departures are only a headway window).
    Service-day based: calls after midnight belong to the previous service day.
    """
    if not stop_ids:
        return {}
    sid = snapshot['snapshot_id']
    wanted = set(stop_ids)
    # Large parameter lists plan slowly; beyond a few hundred stops scan the snapshot once.
    if len(wanted) <= LARGE_STOP_LIST:
        marks = ','.join('?' for _ in wanted)
        scope, params = f' AND stop_id IN ({marks})', tuple(wanted)
    else:
        scope, params = '', ()
    info = {s['stop_id']: s for s in rows(con, 'SELECT stop_id,location_type FROM stops WHERE snapshot_id=?' + scope, (sid, *params))
            if s['stop_id'] in wanted}
    calls = [c for c in rows(con, '''SELECT st.stop_id,st.trip_id,t.service_id FROM stop_times st
        JOIN trips t ON t.snapshot_id=st.snapshot_id AND t.trip_id=st.trip_id
        WHERE st.snapshot_id=?''' + scope.replace('stop_id', 'st.stop_id'), (sid, *params)) if c['stop_id'] in wanted]
    services = active_services(con, sid, service_day) if calls else set()
    frequencies: dict[str, list[dict]] = defaultdict(list)
    trips = sorted({c['trip_id'] for c in calls})
    if trips:
        trip_set = set(trips)
        for f in rows(con, 'SELECT * FROM frequencies WHERE snapshot_id=?', (sid,)):
            if f['trip_id'] in trip_set:
                frequencies[f['trip_id']].append(f)
    covered = bool(snapshot.get('feed_start')) and snapshot['feed_start'] <= service_day.isoformat() <= (snapshot.get('feed_end') or '')
    by_stop: dict[str, list[dict]] = defaultdict(list)
    for c in calls:
        by_stop[c['stop_id']].append(c)
    result = {}
    for stop_id in stop_ids:
        mine = by_stop.get(stop_id, [])
        templates = {c['trip_id'] for c in mine}
        active = [c for c in mine if c['service_id'] in services]
        exact = frequency = 0
        for c in active:
            freq = frequencies.get(c['trip_id'])
            if not freq:
                exact += 1
            for f in freq or []:
                if f['exact_times'] == 1:
                    exact += len(range(f['start_sec'], f['end_sec'], f['headway_secs']))
                else:
                    frequency += 1
        location_type = (info.get(stop_id) or {}).get('location_type') or 0
        reason = None
        if not templates and location_type not in (0, None):
            status, reason = 'unknown', 'Station or entrance record; service is recorded on its platform stops.'
        elif not templates:
            status = 'no_trip_references'
            reason = 'No stop_times in this feed snapshot reference this stop on any date.'
        elif active:
            status = 'scheduled'
        elif not covered:
            status = 'unknown'
            reason = (f"The date is outside this feed's calendar coverage ({snapshot.get('feed_start')}–"
                      f"{snapshot.get('feed_end')}), so the feed cannot show whether service runs.")
        else:
            status = 'not_scheduled_on_date'
            reason = 'Trips call here on other service days only.'
        result[stop_id] = {'status': status, 'label': STATUS_LABELS[status], 'reason': reason,
                           'trip_templates': len(templates), 'active_trip_templates': len({c['trip_id'] for c in active}),
                           'exact_calls_on_date': exact, 'frequency_templates_on_date': frequency,
                           'service_date': service_day.isoformat(), 'basis': 'gtfs_service_day'}
    return result


def stop_quality(con, snapshot: dict, stop_id: str, service_day: date) -> dict:
    stop = one(con, 'SELECT stop_id,lat,lon FROM stops WHERE snapshot_id=? AND stop_id=?', (snapshot['snapshot_id'], stop_id))
    if stop is None:
        raise KeyError('Unknown stop')
    status = stop_service_status(con, snapshot, [stop_id], service_day)[stop_id]
    ids = dict(source_id=snapshot['source_id'], snapshot_id=snapshot['snapshot_id'])
    out = []
    if status['status'] == 'no_trip_references':
        out.append(indicator('publisher', 'stop_without_trips', 'Stop record without trips',
            'The publisher lists this stop, but no trip in this feed snapshot calls at it. It is kept and shown, '
            'not hidden; it does not demonstrate service.', level='caution', evidence=status, **ids))
    elif status['status'] == 'unknown':
        out.append(indicator('coverage', 'stop_status_unknown', status['label'], status['reason'], level='caution',
                             evidence=status, **ids))
    elif status['status'] == 'not_scheduled_on_date':
        out.append(indicator('coverage', 'stop_not_scheduled_on_date', status['label'],
            f"{status['trip_templates']} trip templates call here, none on service day {status['service_date']}.",
            evidence=status, **ids))
    if stop['lat'] is None or stop['lon'] is None:
        out.append(indicator('geometry', 'stop_without_coordinates', 'Stop record has no coordinates',
                             'It cannot be placed on the map or used for proximity.', level='caution', **ids))
    return {'snapshot_id': snapshot['snapshot_id'], 'stop_id': stop_id, 'service': status, 'indicators': out}


# ---------------------------------------------------------------------------
# Route geometry and timing
# ---------------------------------------------------------------------------

def route_quality(con, snapshot: dict, route_id: str) -> dict:
    sid = snapshot['snapshot_id']
    if one(con, 'SELECT route_id FROM routes WHERE snapshot_id=? AND route_id=?', (sid, route_id)) is None:
        raise KeyError('Unknown route')
    ids = dict(source_id=snapshot['source_id'], snapshot_id=sid)
    trips = rows(con, '''SELECT t.trip_id,t.shape_id,(SELECT COUNT(*) FROM shapes sh WHERE sh.snapshot_id=t.snapshot_id
        AND sh.shape_id=t.shape_id) AS points FROM trips t WHERE t.snapshot_id=? AND t.route_id=?''', (sid, route_id))
    timing = timing_summary(con, sid, route_id)
    out = timing_indicators(timing, scope='route', **ids)
    schematic = [t['trip_id'] for t in trips if not t['shape_id'] or (t['points'] or 0) < 2]
    if schematic:
        out.append(indicator('geometry', 'schematic_trips',
            f'{len(schematic)} of {len(trips)} trip templates have no usable source shape',
            'They are drawn as dashed straight links between consecutive stops. These lines are schematic and do '
            'not show the actual path.', evidence={'trip_templates': len(schematic), 'examples': schematic[:5]}, **ids))
    if not trips:
        out.append(indicator('coverage', 'route_without_trips', 'Route record without trips',
            'The publisher lists this route, but no trips reference it. That is a gap in the data, not proof that '
            'the route does not run.', level='caution', **ids))
    return {'snapshot_id': sid, 'route_id': route_id, 'timing': timing, 'indicators': out}


def trip_timing(con, snapshot_id: str, trip_id: str, stops: list[dict]) -> dict:
    route = one(con, '''SELECT r.route_type FROM trips t JOIN routes r ON r.snapshot_id=t.snapshot_id
        AND r.route_id=t.route_id WHERE t.snapshot_id=? AND t.trip_id=?''', (snapshot_id, trip_id))
    result = segment_intervals(stops, route['route_type'] if route else None)
    result['frequency_based'] = one(con, 'SELECT COUNT(*) AS n FROM frequencies WHERE snapshot_id=? AND trip_id=?',
                                    (snapshot_id, trip_id))['n'] > 0
    return result


# ---------------------------------------------------------------------------
# Feed freshness, coverage and validation
# ---------------------------------------------------------------------------

_VOLUME_CACHE: dict[str, dict[str, int]] = {}
_TIMING_CACHE: dict[str, dict] = {}


def daily_trip_volume(con, snapshot: dict) -> dict[str, int]:
    """Active trip templates per service day across observed calendar coverage.

    Cached per immutable snapshot. Frequency trips count once (as templates).
    """
    sid = snapshot['snapshot_id']
    if sid in _VOLUME_CACHE:
        return _VOLUME_CACHE[sid]
    per_service = Counter(t['service_id'] for t in rows(con, 'SELECT service_id FROM trips WHERE snapshot_id=?', (sid,)))
    calendars = rows(con, 'SELECT * FROM calendars WHERE snapshot_id=?', (sid,))
    exceptions: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for e in rows(con, 'SELECT service_id,service_date,exception_type FROM calendar_exceptions WHERE snapshot_id=?', (sid,)):
        exceptions[str(e['service_date'])].append((e['service_id'], e['exception_type']))
    volume = {}
    if snapshot.get('feed_start') and snapshot.get('feed_end'):
        day, end = date.fromisoformat(snapshot['feed_start']), date.fromisoformat(snapshot['feed_end'])
        while day <= end:
            iso, weekday = day.isoformat(), WEEKDAYS[day.weekday()]
            active = {c['service_id'] for c in calendars
                      if c[weekday] == 1 and str(c['start_date']) <= iso <= str(c['end_date'])}
            for service_id, kind in exceptions.get(iso, []):
                (active.add if kind == 1 else active.discard)(service_id)
            volume[iso] = sum(per_service[s] for s in active)
            day += timedelta(days=1)
    _VOLUME_CACHE[sid] = volume
    return volume


def version_date(feed_version: str | None) -> str | None:
    """A YYYYMMDD token in the publisher's free-text version label, if any.

    This is the publisher's label, reported as such; it is not assumed to be
    the publication date, and no date is invented when the label has none.
    """
    for match in re.finditer(r'(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)', feed_version or ''):
        try:
            return date(int(match[1]), int(match[2]), int(match[3])).isoformat()
        except ValueError:
            continue
    return None


def _declared(info: dict, field: str) -> str | None:
    try:
        return service_date(info[field]) if info.get(field) else None
    except (ValueError, KeyError):
        return None


def validation_record(folder: Path, content_sha256: str) -> dict | None:
    """Latest local MobilityData report for exactly this archive, if one exists."""
    best = None
    try:
        evidence_files = list(Path(folder).glob('*/evidence.json'))
    except OSError:
        return None
    for path in evidence_files:
        try:
            evidence = json.loads(path.read_text(encoding='utf-8'))
            if evidence.get('status') != 'report_generated' or evidence.get('input_sha256') != content_sha256:
                continue
            if Path(evidence.get('report_directory', '')).resolve() != path.parent.resolve():
                continue
            if best is None or evidence.get('validated_at_utc', '') > best[0].get('validated_at_utc', ''):
                best = (evidence, path.parent/'report.json')
        except (OSError, ValueError, TypeError):
            continue
    if best is None:
        return None
    try:
        report = json.loads(best[1].read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    totals, rules = Counter(), []
    for notice in report.get('notices', []):
        totals[notice.get('severity')] += notice.get('totalNotices', 0)
        rules.append({'severity': notice.get('severity'), 'code': notice.get('code'), 'count': notice.get('totalNotices', 0)})
    rules.sort(key=lambda r: ({'ERROR': 0, 'WARNING': 1, 'INFO': 2}.get(r['severity'], 3), -r['count']))
    return {'validator_version': best[0].get('validator_version'), 'validation_date': best[0].get('date'),
            'validated_at_utc': best[0].get('validated_at_utc'),
            'errors': totals['ERROR'], 'warnings': totals['WARNING'], 'infos': totals['INFO'], 'rules': rules}


def source_quality(con, snapshot: dict, service_day: date, *, validation_folder: Path | None = None,
                   cache_folder: Path | None = None) -> dict:
    sid, source = snapshot['snapshot_id'], snapshot['source_id']
    ids = dict(source_id=source, snapshot_id=sid)
    present = feed_info_present(snapshot['raw_zip_path'], snapshot['content_sha256'])
    info = declared_info(snapshot['raw_zip_path'], snapshot['content_sha256']) if present else {}
    iso = service_day.isoformat()
    volume = daily_trip_volume(con, snapshot)
    trips_on_date = volume.get(iso) if iso in volume else (
        0 if not snapshot.get('feed_start') else None)
    same_weekday = {d: n for d, n in volume.items() if date.fromisoformat(d).weekday() == service_day.weekday()}
    peak_day = max(same_weekday, key=lambda d: (same_weekday[d], d)) if same_weekday else None
    declared_start, declared_end = _declared(info, 'feed_start_date'), _declared(info, 'feed_end_date')
    label_date = version_date(info.get('feed_version'))
    in_observed = bool(snapshot.get('feed_start')) and snapshot['feed_start'] <= iso <= (snapshot.get('feed_end') or '')
    in_declared = None if not (declared_start or declared_end) else (
        (not declared_start or declared_start <= iso) and (not declared_end or iso <= declared_end))
    freshness = {
        'retrieved_at': snapshot.get('retrieved_at'),
        'retrieval_note': 'When this local copy was downloaded; not a publication date.',
        'feed_info_present': present,
        'publisher_name': info.get('feed_publisher_name') or None,
        'feed_version': info.get('feed_version') or None,
        'version_label_date': label_date,
        'publication_date': None,
        'publication_note': 'GTFS has no publication-date field. The version label is the publisher\'s text; '
                            'a date in it is reported as a label, not a confirmed publication date.',
        'declared_start': declared_start, 'declared_end': declared_end,
        'observed_start': snapshot.get('feed_start'), 'observed_end': snapshot.get('feed_end'),
        'service_date': iso, 'in_observed_coverage': in_observed, 'in_declared_validity': in_declared,
        'trip_templates_on_date': trips_on_date,
        'busiest_same_weekday': {'date': peak_day, 'trip_templates': same_weekday[peak_day]} if peak_day else None,
    }
    out = []
    if not present:
        out.append(indicator('publisher', 'no_feed_info', 'No feed_info.txt',
            'The publisher supplies no feed metadata: publisher, version and declared validity are unknown. '
            'Coverage below comes from the service calendars only.', **ids))
    else:
        if not declared_end:
            out.append(indicator('publisher', 'no_declared_end', 'No declared end date',
                'feed_info.txt has no feed_end_date; how long the publisher intends this timetable to apply is '
                'unknown. Coverage below comes from the service calendars.', **ids))
        if not info.get('feed_version'):
            out.append(indicator('publisher', 'no_feed_version', 'No version label',
                'The feed carries no version label, so its publication date is unknown.', **ids))
    if not in_observed:
        out.append(indicator('coverage', 'date_outside_calendar', 'Selected date is outside the service calendars',
            f"The calendars cover {snapshot.get('feed_start')}–{snapshot.get('feed_end')}. The feed cannot show "
            'whether service runs on this date; absence here is not absence of service.', level='caution', **ids))
    elif in_declared is False:
        out.append(indicator('coverage', 'date_outside_declared', 'Selected date is outside declared validity',
            f'The publisher declares {declared_start or "—"}–{declared_end or "—"}. Calendars still contain '
            'service; treat it as unconfirmed by the publisher.', level='caution', **ids))
    if in_observed and trips_on_date == 0:
        out.append(indicator('coverage', 'no_service_on_date', 'No trips scheduled on this date',
            'The calendars cover this date but schedule no trips in this feed.', **ids))
    if (label_date and trips_on_date and peak_day and in_observed
            and (service_day-date.fromisoformat(label_date)).days > PROJECTION_DAYS
            and trips_on_date <= REDUCED_SHARE*same_weekday[peak_day]):
        days = (service_day-date.fromisoformat(label_date)).days
        share = round(100*trips_on_date/same_weekday[peak_day])
        out.append(indicator('coverage', 'long_range_reduced_volume',
            'Version label dated well before this date, with reduced volume',
            f"The feed's version label is dated {label_date}, {days} days before {iso}. This date has "
            f"{trips_on_date} trip templates, {share}% of the busiest {WEEKDAYS[service_day.weekday()].capitalize()} "
            f"in the feed ({same_weekday[peak_day]} on {peak_day}). Seasonal timetables often differ, and reduced "
            'volume alone does not show missing service. The publisher may since have issued a newer timetable.',
            level='caution', evidence={'version_label_date': label_date, 'trip_templates_on_date': trips_on_date,
                                       'busiest_same_weekday': freshness['busiest_same_weekday']}, **ids))
    timing = cached_feed_timing(con, snapshot, cache_folder)
    out.extend(timing_indicators(timing, scope='feed', **ids))
    record = validation_record(validation_folder, snapshot['content_sha256']) if validation_folder else None
    if record:
        out.append(indicator('validation', 'external_validation',
            f"MobilityData validator v{record['validator_version']}: {record['errors']} ERROR, "
            f"{record['warnings']} WARNING, {record['infos']} INFO notices",
            f"Run locally on this exact archive for date {record['validation_date']}. Zero ERROR notices means the "
            'files are structurally valid GTFS; it does not show that the schedules match real-world operations.',
            evidence=record, **ids))
    else:
        out.append(indicator('validation', 'external_validation_missing', 'No external validation report',
            'No local MobilityData report exists for this exact archive.', **ids))
    return {'snapshot_id': sid, 'source_id': source, 'freshness': freshness, 'timing': timing, 'indicators': out}
