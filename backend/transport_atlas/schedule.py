"""GTFS service-day calculations; never infer exact departures from headways."""
from __future__ import annotations
from datetime import date, datetime, timedelta, timezone, time
from zoneinfo import ZoneInfo
from .storage import rows, one

WEEKDAYS = ("monday","tuesday","wednesday","thursday","friday","saturday","sunday")

def _date(value: str | date) -> date:
    return value if isinstance(value,date) else date.fromisoformat(value)

def gtfs_instant(service_day: date | str, seconds: int, agency_timezone: str) -> datetime:
    """Use GTFS noon-minus-12h reference, including DST changeover days."""
    day = _date(service_day)
    noon = datetime.combine(day,time(12),ZoneInfo(agency_timezone))
    utc_origin = noon.astimezone(timezone.utc)-timedelta(hours=12)
    return (utc_origin+timedelta(seconds=seconds)).astimezone(ZoneInfo(agency_timezone))

def active_services(con, snapshot_id: str, service_day: date | str) -> set[str]:
    d = _date(service_day)
    iso = d.isoformat()
    wd=WEEKDAYS[d.weekday()]
    from_weekly={r['service_id'] for r in rows(con,
        f'SELECT service_id FROM calendars WHERE snapshot_id=? AND start_date<=? AND end_date>=? AND {wd}=1',
        (snapshot_id,iso,iso))}
    for r in rows(con,'SELECT service_id,exception_type FROM calendar_exceptions WHERE snapshot_id=? AND service_date=?',(snapshot_id,iso)):
        if r['exception_type']==1: from_weekly.add(r['service_id'])
        else: from_weekly.discard(r['service_id'])
    return from_weekly

def active_snapshots(con) -> list[dict]:
    return rows(con,"""SELECT s.* FROM feed_snapshots s
      JOIN selection_members m ON m.snapshot_id=s.snapshot_id
      WHERE s.import_status='ready' ORDER BY s.source_id""")

def _freq_rows(con, snapshot_id, trip_id):
    return rows(con,"SELECT * FROM frequencies WHERE snapshot_id=? AND trip_id=? ORDER BY start_sec",(snapshot_id,trip_id))

def _base_departure(con, snapshot_id, trip_id) -> int | None:
    row=one(con,"SELECT departure_sec,arrival_sec FROM stop_times WHERE snapshot_id=? AND trip_id=? ORDER BY stop_sequence LIMIT 1",(snapshot_id,trip_id))
    if row is None: return None
    return row['departure_sec'] if row['departure_sec'] is not None else row['arrival_sec']

def trip_departure_offsets(con, snapshot_id, trip_id, stop_sec):
    """Return exact stop time(s), and non-exact frequency window descriptors separately."""
    freqs = _freq_rows(con,snapshot_id,trip_id)
    if not freqs:
        return ([stop_sec] if stop_sec is not None else []), []
    base = _base_departure(con,snapshot_id,trip_id)
    if base is None or stop_sec is None:
        return [], [dict(precision='unknown',reason='Missing template stop time')]
    offset=stop_sec-base
    exact=[]
    windows=[]
    for f in freqs:
        if f['exact_times']==1:
            exact.extend(range(f['start_sec']+offset, f['end_sec']+offset, f['headway_secs']))
        else:
            windows.append(dict(start_sec=f['start_sec']+offset,
                end_sec=f['end_sec']+offset,headway_secs=f['headway_secs'],
                precision='frequency',estimated_services=(f['end_sec']-f['start_sec']+f['headway_secs']-1)//f['headway_secs']))
    return exact,windows

def daily_summary(con, service_day: date | str) -> dict:
    day=_date(service_day)
    results=[]
    for snapshot in active_snapshots(con):
        sid=snapshot['snapshot_id']
        services=active_services(con,sid,day)
        trips=rows(con,"SELECT trip_id,route_id FROM trips WHERE snapshot_id=?",(sid,))
        active=rows(con,"SELECT trip_id,route_id,service_id FROM trips WHERE snapshot_id=?",(sid,))
        active=[t for t in active if t['service_id'] in services]
        scheduled=0
        estimated_windows=0
        for t in active:
            freqs=_freq_rows(con,sid,t['trip_id'])
            if not freqs:
                scheduled += 1
            else:
                scheduled += sum(len(range(f['start_sec'],f['end_sec'],f['headway_secs'])) for f in freqs if f['exact_times']==1)
                estimated_windows += sum(1 for f in freqs if f['exact_times']==0)
        results.append(dict(source_id=snapshot['source_id'],snapshot_id=sid,
            feed_start=snapshot['feed_start'],feed_end=snapshot['feed_end'],
            service_date_covered=snapshot['feed_start']<=day.isoformat()<=snapshot['feed_end'],
            total_route_records=one(con,'SELECT COUNT(*) AS n FROM routes WHERE snapshot_id=?',(sid,))['n'],
            total_stop_records=one(con,'SELECT COUNT(*) AS n FROM stops WHERE snapshot_id=?',(sid,))['n'],
            active_trip_templates=len(active),
            active_route_records=len({t['route_id'] for t in active}),
            scheduled_trip_instances=scheduled,
            non_exact_frequency_windows=estimated_windows,
            precision_note='Frequency windows excluded from exact departure count'))
    return dict(date=day.isoformat(),snapshots=results)

def stop_board(con, snapshot_id: str, stop_id: str, wall_date: date | str,
               *, start_hour: int = 0, end_hour: int = 24) -> dict:
    """Board by stop-local calendar date, including previous-service-day overnight trips."""
    if not (0<=start_hour<end_hour<=48):
        raise ValueError('Expected 0 <= start_hour < end_hour <= 48')
    d=_date(wall_date)
    stop=one(con,"SELECT * FROM stops WHERE snapshot_id=? AND stop_id=?",(snapshot_id,stop_id))
    if stop is None: raise KeyError(f'Unknown stop: {snapshot_id}/{stop_id}')
    stop_rows=rows(con,"""SELECT t.trip_id,t.route_id,t.service_id,t.headsign,
      r.short_name,r.long_name,st.departure_sec,st.pickup_type,a.timezone AS agency_timezone
      FROM stop_times st JOIN trips t ON st.snapshot_id=t.snapshot_id AND st.trip_id=t.trip_id
      JOIN routes r ON r.snapshot_id=t.snapshot_id AND r.route_id=t.route_id
      JOIN agencies a ON a.snapshot_id=r.snapshot_id AND a.agency_id=r.agency_id
      WHERE st.snapshot_id=? AND st.stop_id=? AND st.departure_sec IS NOT NULL""",(snapshot_id,stop_id))
    # Fetch maximal event offset for a conservative previous-service-day lookback.
    mx=one(con,"SELECT MAX(departure_sec) AS n FROM stop_times WHERE snapshot_id=?",(snapshot_id,))['n'] or 0
    mf=one(con,"SELECT MAX(end_sec) AS n FROM frequencies WHERE snapshot_id=?",(snapshot_id,))['n'] or 0
    max_days=(max(mx,mf)//86400)+2
    if max_days > 14:
        raise ValueError('Feed contains exceptionally long trip times; explicit extended horizon support needed')
    start_local = datetime.combine(d,time(0))+timedelta(hours=start_hour)
    end_local = datetime.combine(d,time(0))+timedelta(hours=end_hour)
    exact_departures=[]
    freq_windows=[]
    active_cache={}
    for service_d in (d-timedelta(days=k) for k in range(max_days-1,-2,-1)):
        for item in stop_rows:
            cachekey=(service_d,item['service_id'])
            if service_d not in active_cache:
                active_cache[service_d]=active_services(con,snapshot_id,service_d)
            if item['service_id'] not in active_cache[service_d]:
                continue
            zone=item['agency_timezone']
            display_zone=stop['timezone'] or zone
            exact,windows=trip_departure_offsets(con,snapshot_id,item['trip_id'],item['departure_sec'])
            for seconds in exact:
                instant=gtfs_instant(service_d,seconds,zone).astimezone(ZoneInfo(display_zone))
                local=instant.replace(tzinfo=None)
                if start_local<=local<end_local:
                    exact_departures.append(dict(snapshot_id=snapshot_id,stop_id=stop_id,
                        trip_id=item['trip_id'],route_id=item['route_id'],route_short_name=item['short_name'],
                        route_long_name=item['long_name'],headsign=item['headsign'],
                        service_date=service_d.isoformat(),gtfs_time=f'{seconds//3600:02d}:{(seconds//60)%60:02d}:{seconds%60:02d}',
                        local_departure=instant.isoformat(),timezone=display_zone,
                        boarding_allowed=item['pickup_type'] != 1,precision='scheduled'))
            for window in windows:
                if window['precision'] != 'frequency':continue
                st=gtfs_instant(service_d,window['start_sec'],zone).astimezone(ZoneInfo(display_zone))
                en=gtfs_instant(service_d,window['end_sec'],zone).astimezone(ZoneInfo(display_zone))
                if st.replace(tzinfo=None)<end_local and en.replace(tzinfo=None)>start_local:
                    freq_windows.append(dict(snapshot_id=snapshot_id,stop_id=stop_id,
                        trip_id=item['trip_id'],route_id=item['route_id'],headsign=item['headsign'],
                        service_date=service_d.isoformat(),start_local=st.isoformat(),end_local=en.isoformat(),
                        headway_seconds=window['headway_secs'],precision='frequency_not_exact'))
    exact_departures.sort(key=lambda x:x['local_departure'])
    return dict(stop=stop,wall_date=d.isoformat(),start_hour=start_hour,end_hour=end_hour,
                departures=exact_departures,frequency_windows=freq_windows,
                warning='Frequency windows provide headways only; their precise departure times are unknown.')

def trip_stop_times(con, snapshot_id: str, trip_id: str) -> dict:
    trip=one(con,'SELECT * FROM trips WHERE snapshot_id=? AND trip_id=?',(snapshot_id,trip_id))
    if trip is None: raise KeyError('Unknown trip')
    return dict(trip=trip,stops=rows(con,"""SELECT st.*,s.name,s.lat,s.lon
      FROM stop_times st JOIN stops s ON s.snapshot_id=st.snapshot_id AND s.stop_id=st.stop_id
      WHERE st.snapshot_id=? AND st.trip_id=? ORDER BY st.stop_sequence""",(snapshot_id,trip_id)))
