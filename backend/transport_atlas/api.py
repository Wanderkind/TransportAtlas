"""Read-only local API; administrative imports deliberately remain CLI-only."""
from __future__ import annotations
import os
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from functools import lru_cache
from threading import Lock, Thread
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, JSONResponse
from .storage import connect, rows, one
from .schedule import daily_summary, stop_board, trip_stop_times, active_services, active_snapshots
from .network import network_geometry, MODE_TYPES, SHAPE_CACHE_LIMIT
from . import shape_store
from .timetable import route_timetable
from .nearby import nearby_stops, stop_routes
from . import quality
from .history import SnapshotHistory, HistoryError, NotFound
from .provenance import declared_info
from .network_cache import cache_key, read_cache, write_cache

DEFAULT_DB=Path(os.getenv('TRANSPORT_ATLAS_DB','data/transport_atlas.duckdb'))

def create_app(db_path: str | Path | None = None,backend: str | None = None):
    target=Path(db_path) if db_path is not None else DEFAULT_DB
    @asynccontextmanager
    async def lifespan(app):
        app.state.con=connect(target,backend)
        # Read-only history service; never changes selection_members.
        app.state.history=SnapshotHistory(app.state.con,validation_folder=target.parent/'local-verification'/'phase4-validator')
        try: yield
        finally: app.state.con.close()
    app=FastAPI(title='Transport Atlas',version='0.1.0',lifespan=lifespan)
    shape_cache = {}
    hydrated = set()
    geometry_lock = Lock()
    shape_write_lock = Lock()

    def write_shape_levels(folder, documents):
        # Serializing tens of MB must not delay the geometry response.
        with shape_write_lock:
            for name, document in documents:
                shape_store.write(folder/name, document)

    @lru_cache(maxsize=16)
    def cached_network(selection, day, mode, source, sources, active_only, max_routes, max_points):
        key = cache_key(selection, day, mode, source, sources, active_only, max_routes, max_points)
        folder = target.parent/'cache'/'network'
        existing = read_cache(folder, key)
        if existing is not None:
            return existing
        # Serialize only cold builds touching the shared shape cache. Ready memory
        # and disk responses must not wait behind a superseded uncached view.
        with geometry_lock:
            existing = read_cache(folder,key)
            if existing is not None:
                return existing
            # Reuse simplified shape levels persisted for these immutable snapshots.
            shape_folder = target.parent/'cache'/'shapes'
            involved = [s for s in active_snapshots(app.state.con)
                        if (not source or s['source_id'] == source)
                        and (sources is None or s['source_id'] in sources.split(','))]
            before = {}
            for snapshot in involved:
                if snapshot['snapshot_id'] in hydrated:
                    before[snapshot['snapshot_id']] = shape_store.inventory(shape_cache, snapshot['snapshot_id'])
                else:
                    before[snapshot['snapshot_id']] = shape_store.hydrate(shape_cache, shape_folder, snapshot, limit=SHAPE_CACHE_LIMIT)
                    hydrated.add(snapshot['snapshot_id'])
            geometry = network_geometry(app.state.con, day, source_id=source,
                                source_ids=set(sources.split(',')) if sources is not None else None,
                                shape_cache=shape_cache, mode=mode, active_only=active_only,
                                max_routes=max_routes, max_points=max_points)
            documents = [d for d in (shape_store.capture(shape_cache, snapshot, before[snapshot['snapshot_id']])
                                     for snapshot in involved) if d is not None]
            if documents:
                Thread(target=write_shape_levels, args=(shape_folder, documents), daemon=True,
                       name='shape-level-cache-writer').start()
            return write_cache(folder, key, geometry)
    app.add_middleware(CORSMiddleware,allow_origins=['http://127.0.0.1:5173','http://localhost:5173'],allow_credentials=False,allow_methods=['GET'],allow_headers=['*'])

    @app.get('/api/v1/health')
    def health(): return {'status':'ok','database_backend':backend or os.getenv('TRANSPORT_ATLAS_DB_BACKEND','duckdb')}

    @app.get('/api/v1/snapshots')
    def snapshots():
        return [dict(s, feed_info=declared_info(s['raw_zip_path'],s['content_sha256'])) for s in active_snapshots(app.state.con)]

    @app.get('/api/v1/summary')
    def summary(date_value: date = Query(alias='date')):
        return daily_summary(app.state.con,date_value)

    @app.get('/api/v1/routes')
    def routes(date_value: date = Query(alias='date'),limit: int=Query(default=5000,ge=1,le=20000)):
        c=app.state.con
        result=[]
        for snap in active_snapshots(c):
            sid=snap['snapshot_id']
            services=active_services(c,sid,date_value)
            route_rows=rows(c,'SELECT * FROM routes WHERE snapshot_id=? ORDER BY short_name,long_name LIMIT ?',(sid,limit))
            active_route_ids={t['route_id'] for t in rows(c,'SELECT route_id,service_id FROM trips WHERE snapshot_id=?',(sid,)) if t['service_id'] in services}
            for r in route_rows:
                r['active_on_date']=r['route_id'] in active_route_ids
                r['source_id']=snap['source_id']
                result.append(r)
        return {'date':date_value.isoformat(),'routes':result,'warning': 'Per-source route limit applied' if len(result)>=limit else None}

    @app.get('/api/v1/stops')
    def stops(limit: int=Query(default=8000,ge=1,le=20000), source: str | None = None,
              mode: str = 'all', sources: str | None = None, date_value: date | None = Query(default=None, alias='date'),
              active_only: bool = False, search: str = ''):
        if mode not in MODE_TYPES:
            raise HTTPException(status_code=422, detail='Unknown transport mode')
        if active_only and date_value is None:
            raise HTTPException(status_code=422, detail='Active stops require a service date')
        c=app.state.con
        result=[]
        for s in active_snapshots(c):
            if source and source != s['source_id']:
                continue
            if sources is not None and s['source_id'] not in sources.split(','):
                continue
            sid = s['snapshot_id']
            services = active_services(c, sid, date_value) if active_only else None
            if services is not None and not services:
                continue
            conditions = ['s.snapshot_id=?', 's.lat IS NOT NULL', 's.lon IS NOT NULL']
            params = [sid]
            scope = []
            if MODE_TYPES[mode] is not None:
                values = sorted(MODE_TYPES[mode])
                scope.append('r.route_type IN (' + ','.join('?' for _ in values) + ')')
                params.extend(values)
            if services is not None:
                values = sorted(services)
                scope.append('t.service_id IN (' + ','.join('?' for _ in values) + ')')
                params.extend(values)
            conditions.extend(scope)
            entries=rows(c, '''SELECT DISTINCT s.* FROM stops s JOIN stop_times st
                ON st.snapshot_id=s.snapshot_id AND st.stop_id=s.stop_id JOIN trips t
                ON t.snapshot_id=st.snapshot_id AND t.trip_id=st.trip_id JOIN routes r
                ON r.snapshot_id=t.snapshot_id AND r.route_id=t.route_id
                WHERE ''' + ' AND '.join(conditions) + ' ORDER BY s.name,s.stop_id', tuple(params))
            result.extend(dict(st, source_id=s['source_id']) for st in entries
                          if not search or search.casefold() in f"{st['name']} {st['stop_id']}".casefold())
        truncated = len(result) > limit
        return JSONResponse({'stops':result[:limit], 'total':len(result), 'truncated':truncated,
                'warning':'Stop limit reached; narrow source, mode or search filters.' if truncated else None})

    @app.get('/api/v1/route/{snapshot_id}/{route_id}/geometry')
    def route_geometry(snapshot_id: str,route_id: str):
        c=app.state.con
        r=one(c,'SELECT * FROM routes WHERE snapshot_id=? AND route_id=?',(snapshot_id,route_id))
        if not r: raise HTTPException(status_code=404,detail='Route not found')
        trips=rows(c,'SELECT trip_id,shape_id FROM trips WHERE snapshot_id=? AND route_id=?',(snapshot_id,route_id))
        features=[]
        done_shapes=set()
        schematic=set()
        for trip in trips:
            shape=trip['shape_id']
            if shape and shape in done_shapes:
                continue
            if shape:
                pts=rows(c,'SELECT lon,lat FROM shapes WHERE snapshot_id=? AND shape_id=? ORDER BY point_sequence',(snapshot_id,shape))
                done_shapes.add(shape)
                if len(pts)>=2:
                    features.append({'type':'Feature','properties':{'snapshot_id':snapshot_id,'route_id':route_id,'shape_id':shape,'quality':'source-shape'},'geometry':{'type':'LineString','coordinates':[[p['lon'],p['lat']] for p in pts]}})
                    continue
            pts=rows(c,"""SELECT s.lon,s.lat FROM stop_times st JOIN stops s
                 ON s.snapshot_id=st.snapshot_id AND s.stop_id=st.stop_id
                 WHERE st.snapshot_id=? AND st.trip_id=? AND s.lat IS NOT NULL AND s.lon IS NOT NULL
                 ORDER BY st.stop_sequence""",(snapshot_id,trip['trip_id']))
            coords=tuple((p['lon'],p['lat']) for p in pts)
            if len(coords)>=2 and coords not in schematic:
                schematic.add(coords)
                features.append({'type':'Feature','properties':{'snapshot_id':snapshot_id,'route_id':route_id,'shape_id':None,'quality':'schematic-stop-link'},'geometry':{'type':'LineString','coordinates':coords}})
        return {'type':'FeatureCollection','features':features}

    @app.get('/api/v1/network/geometry')
    def complete_network(date_value: date = Query(alias='date'), mode: str = 'all',
                         source: str | None = None, sources: str | None = None, active_only: bool = True,
                         max_routes: int = Query(default=1200, ge=1, le=5000),
                         max_points: int = Query(default=120000, ge=1000, le=300000)):
        if mode not in MODE_TYPES:
            raise HTTPException(status_code=422, detail='Unknown transport mode')
        selected = active_snapshots(app.state.con)
        selection = tuple(s['snapshot_id'] for s in selected)
        if sources is not None and set(sources.split(',')) == {s['source_id'] for s in selected}:
            sources = None
        payload = cached_network(selection, date_value, mode, source, sources, active_only, max_routes, max_points)
        return Response(content=payload, media_type='application/geo+json')

    @app.get('/api/v1/route/{snapshot_id}/{route_id}/timetable')
    def route_schedule(snapshot_id: str, route_id: str,
                       date_value: date = Query(alias='date'),
                       limit: int = Query(default=100, ge=1, le=500),
                       offset: int = Query(default=0, ge=0),
                       direction: str = 'all', variant: str = 'all'):
        try:
            return route_timetable(app.state.con, snapshot_id, route_id, date_value, limit=limit,
                                   offset=offset, direction=direction, variant=variant)
        except KeyError:
            raise HTTPException(status_code=404, detail='Route not found')

    @app.get('/api/v1/stop/{snapshot_id}/{stop_id}/nearby')
    def nearby(snapshot_id: str, stop_id: str, radius: float = Query(default=200, ge=10, le=1000),
               limit: int = Query(default=30, ge=1, le=200), date_value: date | None = Query(default=None, alias='date')):
        try:
            result = nearby_stops(app.state.con, snapshot_id, stop_id, radius_m=radius, limit=limit)
        except KeyError:
            raise HTTPException(status_code=404, detail='Stop not found')
        if date_value is not None:
            # Service status per source snapshot, for the origin and every listed record.
            snapshots = {s['snapshot_id']: s for s in active_snapshots(app.state.con)}
            wanted: dict[str, list[str]] = {}
            for item in [{'snapshot_id': snapshot_id, 'stop_id': stop_id}, *result['stops']]:
                wanted.setdefault(item['snapshot_id'], []).append(item['stop_id'])
            status = {}
            for sid, ids in wanted.items():
                snap = snapshots.get(sid) or one(app.state.con, 'SELECT * FROM feed_snapshots WHERE snapshot_id=?', (sid,))
                for stop, value in quality.stop_service_status(app.state.con, snap, ids, date_value).items():
                    status[(sid, stop)] = value
            result['origin']['service'] = status[(snapshot_id, stop_id)]
            for item in result['stops']:
                item['service'] = status[(item['snapshot_id'], item['stop_id'])]
            result['service_date'] = date_value.isoformat()
        return result

    def snapshot_or_404(snapshot_id):
        snap = one(app.state.con, 'SELECT * FROM feed_snapshots WHERE snapshot_id=?', (snapshot_id,))
        if snap is None:
            raise HTTPException(status_code=404, detail='Snapshot not found')
        return snap

    @app.get('/api/v1/quality/source/{snapshot_id}')
    def source_quality(snapshot_id: str, date_value: date = Query(alias='date')):
        return quality.source_quality(app.state.con, snapshot_or_404(snapshot_id), date_value,
                                      validation_folder=target.parent/'local-verification'/'phase4-validator',
                                      cache_folder=target.parent/'cache'/'quality')

    @app.get('/api/v1/quality/route/{snapshot_id}/{route_id}')
    def route_quality(snapshot_id: str, route_id: str):
        try:
            return quality.route_quality(app.state.con, snapshot_or_404(snapshot_id), route_id)
        except KeyError:
            raise HTTPException(status_code=404, detail='Route not found')

    @app.get('/api/v1/quality/stop/{snapshot_id}/{stop_id}')
    def stop_quality(snapshot_id: str, stop_id: str, date_value: date = Query(alias='date')):
        try:
            return quality.stop_quality(app.state.con, snapshot_or_404(snapshot_id), stop_id, date_value)
        except KeyError:
            raise HTTPException(status_code=404, detail='Stop not found')

    @app.get('/api/v1/stop/{snapshot_id}/{stop_id}/routes')
    def routes_at_stop(snapshot_id: str, stop_id: str, date_value: date = Query(alias='date')):
        try:
            return stop_routes(app.state.con, snapshot_id, stop_id, date_value)
        except KeyError:
            raise HTTPException(status_code=404, detail='Stop not found')

    @app.get('/api/v1/stop/{snapshot_id}/{stop_id}/departures')
    def departures(snapshot_id: str,stop_id: str,date_value: date=Query(alias='date'),start_hour:int=0,end_hour:int=24):
        try:return stop_board(app.state.con,snapshot_id,stop_id,date_value,start_hour=start_hour,end_hour=end_hour)
        except KeyError:raise HTTPException(status_code=404,detail='Stop not found')
        except ValueError as exc:raise HTTPException(status_code=422,detail=str(exc))

    @app.get('/api/v1/history/sources')
    def history_sources():
        return {'sources': app.state.history.sources()}

    @app.get('/api/v1/history/{source_id}/snapshots')
    def history_snapshots(source_id: str, date_value: date | None = Query(default=None, alias='date'),
                          verify_archives: bool = False, sort: str = 'retrieved'):
        try:
            return app.state.history.inventory(source_id, date_value, verify_archives=verify_archives, sort=sort)
        except NotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0]))
        except HistoryError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.get('/api/v1/history/{source_id}/compare')
    def history_compare(source_id: str, baseline: str, comparison: str, policy: str = 'same_date',
                        date_value: date | None = Query(default=None, alias='date'),
                        baseline_date: date | None = None, comparison_date: date | None = None,
                        record_status: str = 'changed', record_limit: int = Query(default=100, ge=1, le=1000),
                        record_offset: int = Query(default=0, ge=0)):
        try:
            return app.state.history.compare(source_id, baseline, comparison, policy=policy, service_date=date_value,
                                             baseline_date=baseline_date, comparison_date=comparison_date,
                                             record_status=record_status, record_limit=record_limit,
                                             record_offset=record_offset)
        except NotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0]))
        except HistoryError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.get('/api/v1/history/{source_id}/timetable-diff')
    def history_timetable_diff(source_id: str, baseline: str, comparison: str, policy: str = 'same_date',
                               date_value: date | None = Query(default=None, alias='date'),
                               baseline_date: date | None = None, comparison_date: date | None = None,
                               status: str = 'differences', route: str | None = None,
                               limit: int = Query(default=100, ge=1, le=500), offset: int = Query(default=0, ge=0)):
        try:
            return app.state.history.timetable_diff(source_id, baseline, comparison, policy=policy,
                                                    service_date=date_value, baseline_date=baseline_date,
                                                    comparison_date=comparison_date, status=status, route=route,
                                                    limit=limit, offset=offset)
        except NotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0]))
        except HistoryError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.get('/api/v1/history/{source_id}/timetable-trip')
    def history_timetable_trip(source_id: str, baseline: str, comparison: str, baseline_trip: str | None = None,
                               comparison_trip: str | None = None):
        try:
            return app.state.history.timetable_trip(source_id, baseline, comparison, baseline_trip=baseline_trip,
                                                    comparison_trip=comparison_trip)
        except NotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc.args[0]))
        except HistoryError as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    @app.get('/api/v1/trip/{snapshot_id}/{trip_id}')
    def trip(snapshot_id:str,trip_id:str):
        try:result=trip_stop_times(app.state.con,snapshot_id,trip_id)
        except KeyError:raise HTTPException(status_code=404,detail='Trip not found')
        result['timing']=quality.trip_timing(app.state.con,snapshot_id,trip_id,result['stops'])
        return result
    return app

app=create_app()
