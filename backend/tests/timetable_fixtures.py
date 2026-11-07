"""Phase 8A: two invented versions of one fictional operator, built to exercise every
per-trip timetable-difference case on Friday 2026-11-06. Not real data.
"""
from __future__ import annotations

from pathlib import Path

from .history_fixtures import _stop, _zip

SOURCE = 'synthetic_timetable'
DAY = '2026-11-06'          # Friday, inside both coverages
SATURDAY = '2026-11-07'     # no service in either version
OUTSIDE = '2027-02-01'      # outside both coverages
STOPS = [_stop(f'S{i}', f'SYNTHETIC Stop {i}', 40 + i / 100, 9.0) for i in range(1, 9)]


def _hms(minutes: int) -> str:
    return f'{minutes // 60:02d}:{minutes % 60:02d}:00'


def _trip(trip_id, route, start_min, stops, *, service='WK', shape='', gaps=None, headsign=None, direction='0'):
    gaps = gaps or [5] * (len(stops) - 1)
    times, t = [], start_min
    for i, stop in enumerate(stops):
        if i:
            t += gaps[i - 1]
        times.append(dict(trip_id=trip_id, arrival_time=_hms(t), departure_time=_hms(t), stop_id=stop,
                          stop_sequence=str(i + 1)))
    return (dict(route_id=route, service_id=service, trip_id=trip_id, trip_headsign=headsign or f'to {stops[-1]}',
                 direction_id=direction, shape_id=shape), times)


def _feed(version: str) -> dict:
    a = version == 'A'
    trips = [
        # identical timetable, same ID, only the shape ID differs
        _trip('T_SAME', 'R1', 7 * 60, ['S1', 'S2', 'S3'], shape='SH1' if a else 'SH9'),
        # same ID, one intermediate time changed (+2 min at the second stop)
        _trip('T_TIMES', 'R1', 7 * 60 + 30, ['S1', 'S2', 'S3'], gaps=[5, 5] if a else [7, 3]),
        # renumbered trip ID, identical timetable
        _trip('OLDREN' if a else 'NEWREN', 'R2', 8 * 60, ['S2', 'S3', 'S4']),
        # renumbered and shifted by 5 minutes
        _trip('SHIFT_A' if a else 'SHIFT_B', 'R3', 9 * 60 + (0 if a else 5), ['S3', 'S4', 'S5']),
        # same ID, one stop added
        _trip('ADDSTOP', 'R4', 9 * 60 + 30, ['S1', 'S2', 'S3'] if a else ['S1', 'S2', 'S7', 'S3']),
        # overnight, same ID; last arrival beyond 24:00 moves by 5 minutes
        _trip('NIGHT', 'R1', 23 * 60 + 50, ['S1', 'S2', 'S3'], gaps=[40, 40] if a else [40, 45]),
        # frequency-only template (exact_times=0) in both
        _trip('FREQ', 'R1', 10 * 60, ['S1', 'S2']),
        # trip whose service a calendar_dates removal cancels on DAY in version B only
        _trip('EXC', 'R8', 15 * 60, ['S6', 'S7'], service='XS'),
    ]
    if a:
        trips += [_trip('AMB_A', 'R5', 10 * 60, ['S1', 'S2', 'S3']),            # two equally good candidates
                  _trip('GONE', 'R6', 12 * 60, ['S4', 'S5', 'S6'])]             # genuinely removed record
    else:
        trips += [_trip('AMB_B1', 'R5', 10 * 60 + 5, ['S1', 'S2', 'S3']),
                  _trip('AMB_B2', 'R5', 10 * 60 - 5, ['S1', 'S2', 'S3']),
                  _trip('NEWTRIP', 'R7', 14 * 60, ['S5', 'S6', 'S8']),          # genuinely added record
                  _trip('EXTRA', 'R8', 16 * 60, ['S6', 'S8'], service='ADD')]   # only via calendar_dates addition
    routes = sorted({t[0]['route_id'] for t in trips})
    week = dict(monday='1', tuesday='1', wednesday='1', thursday='1', friday='1', saturday='0', sunday='0')
    calendar = [dict(service_id='WK', **week, start_date='20260901', end_date='20261231'),
                dict(service_id='XS', **week, start_date='20260901', end_date='20261231')]
    calendar_dates = [] if a else [dict(service_id='XS', date='20261106', exception_type='2'),
                                   dict(service_id='ADD', date='20261106', exception_type='1')]
    return {
        'agency': [dict(agency_id='TT', agency_name='SYNTHETIC Timetable Lines', agency_url='https://example.invalid',
                        agency_timezone='Europe/Rome')],
        'routes': [dict(route_id=r, agency_id='TT', route_short_name=r, route_long_name=f'SYNTHETIC {r}', route_type='3')
                   for r in routes],
        'stops': STOPS,
        'trips': [t[0] for t in trips],
        'stop_times': [row for t in trips for row in t[1]],
        'calendar': calendar,
        'calendar_dates': calendar_dates,
        'frequencies': [dict(trip_id='FREQ', start_time='10:00:00', end_time='11:00:00', headway_secs='600',
                             exact_times='0')],
    }


def build(folder: Path) -> dict[str, Path]:
    folder = Path(folder)
    return {'A': _zip(folder / 'timetable_A.zip', _feed('A')), 'B': _zip(folder / 'timetable_B.zip', _feed('B'))}
