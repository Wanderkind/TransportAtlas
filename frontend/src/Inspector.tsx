import {useEffect, useLayoutEffect, useState} from 'react';
import type {RouteTimetable as Timetable} from './api';
import {endpoints, NetworkGeometry, Route, RouteTimetable, Snapshot, Stop, StopBoard, TimetableTrip, TripDetail} from './api';
import {useKeyedRequest} from './useKeyedRequest';

const clock=(s:string|null|undefined)=>s?.slice(11,16)||'—';
const gtfs=(s:number|null,offset=0)=>s===null?'—':`${Math.floor((s+offset)/3600).toString().padStart(2,'0')}:${Math.floor(((s+offset)%3600)/60).toString().padStart(2,'0')}`;

function Provenance({snapshot}:{snapshot:Snapshot|undefined}){
 if(!snapshot)return null;
 return <details className="provenance"><summary>Feed validity and source</summary>
   <p>{snapshot.source_id} · Calendar coverage {snapshot.feed_start}–{snapshot.feed_end}</p>
   <p>Publisher-declared validity: {snapshot.feed_info?.feed_start_date||'unspecified'}–{snapshot.feed_info?.feed_end_date||'unspecified'}</p>
   <p>Retrieved {snapshot.retrieved_at}</p><p>SHA-256 <code>{snapshot.content_sha256}</code></p>
   <small>Coverage describes the imported feed; missing records do not establish absence of service.</small></details>;
}

export function Inspector({route,stop,date,timetable,board,timetableError,boardError,snapshots,onGeometry,onDirection,onStop}:{
 route:Route|null;stop:Stop|null;date:string;timetable:RouteTimetable|null;board:StopBoard|null;
 timetableError:string|null;boardError:string|null;snapshots:Snapshot[];onGeometry:(g:NetworkGeometry|null)=>void;onDirection:(d:string)=>void;onStop:(s:Stop)=>void}){
 const [direction,setDirection]=useState('all'),[variant,setVariant]=useState('all');
 const [instance,setInstance]=useState<TimetableTrip|null>(null);
 const [tripId,setTripId]=useState('');
 const tripRequest=useKeyedRequest<TripDetail>(route&&tripId?endpoints.trip(route.snapshot_id,tripId):null,`${route?.snapshot_id}|${route?.route_id}|${date}|${tripId}`);
 const trip=tripRequest.data,busy=tripRequest.status==='loading',error=tripRequest.error;
 useEffect(()=>{onDirection('all')},[onDirection]);
 useLayoutEffect(()=>{onGeometry(trip?.geometry??null)},[trip,onGeometry]);
 const variants=timetable?.variants.filter(v=>direction==='all'||String(v.direction_id)===direction)??[];
 // Filtered views and later pages come from the server, which filters the
 // complete instant-ordered list before paging; pages chain by next_offset.
 const filtered=direction!=='all'||variant!=='all';
 const pageKey=`${route?.snapshot_id}|${route?.route_id}|${date}|${direction}|${variant}`;
 const [store,setStore]=useState<{key:string;pages:Record<number,Timetable>}>({key:'',pages:{}});
 const [wanted,setWanted]=useState<{key:string;offset:number}|null>(null);
 const pages=store.key===pageKey?store.pages:{};
 const base=filtered?pages[0]:timetable??undefined;
 const chain:Timetable[]=[];
 for(let page=base;page;page=page.next_offset!=null?pages[page.next_offset]:undefined)chain.push(page);
 const last=chain.at(-1);
 const target=!timetable?null:filtered&&!pages[0]?0:wanted?.key===pageKey&&last?.next_offset===wanted.offset?wanted.offset:null;
 const pageRequest=useKeyedRequest<Timetable>(route&&target!==null?endpoints.timetablePage(route,date,direction,variant,target):null,`${pageKey}|${target}`);
 useEffect(()=>{
   if(pageRequest.status==='ready'&&pageRequest.data&&target!==null){
     const page=pageRequest.data;
     setStore(s=>({key:pageKey,pages:{...(s.key===pageKey?s.pages:{}),[target]:page}}));
   }
 },[pageRequest.status,pageRequest.data,target,pageKey]);
 const trips=chain.flatMap(p=>p.departures).filter(t=>(direction==='all'||String(t.direction_id)===direction)&&(variant==='all'||t.variant_id===variant));
 const matching=last?.matching_departures??timetable?.total_departures??0;
 const pageLoading=pageRequest.status==='loading';
 const windows=(base??timetable)?.frequency_windows.filter(t=>(direction==='all'||String(t.direction_id)===direction)&&(variant==='all'||t.variant_id===variant))??[];
 const chosen=variants.find(v=>v.variant_id===variant);
 const offset=instance?instance.departure_seconds-instance.template_first_seconds:0;
 const snapshot=snapshots.find(s=>s.snapshot_id===(route?.snapshot_id??stop?.snapshot_id));
 return <><div className="inspector-heading">INSPECTOR</div>{route?<>
   <div className="inspect-label" data-route-uid={`${route.snapshot_id}:${route.route_id}`} data-service-date={date}>ROUTE</div><h2>{route.short_name||route.route_id}</h2><p>{route.long_name}</p>
   <p>{route.source_id} · {date} · {route.active_on_date?'Active':'Inactive on selected date'}</p>
   <Provenance snapshot={snapshot}/>
   {timetableError?<p role="alert">Route timetable unavailable: {timetableError}</p>:!timetable?<p role="status">Loading route timetable…</p>:<>
    <p><b>{timetable.total_departures}</b> exact scheduled trips · first {clock(timetable.first_departure)} · last {clock(timetable.last_departure)}</p>
    <label className="direction-picker">Direction <select aria-label="Direction" value={direction} onChange={e=>{setDirection(e.target.value);onDirection(e.target.value);setVariant('all');setTripId('');setInstance(null)}}>
      <option value="all">All directions</option>{Array.from(new Set(timetable.variants.map(v=>String(v.direction_id)))).map(d=><option key={d} value={d}>{d==='null'?'Unspecified':`Direction ${d}`}</option>)}
    </select></label>
    <label className="direction-picker">Variant <select aria-label="Variant" value={variant} onChange={e=>{
      const v=variants.find(v=>v.variant_id===e.target.value);setVariant(e.target.value);setInstance(null);setTripId(v?.representative_trip_id??'');
    }}><option value="all">All stop sequences ({variants.length})</option>{variants.map((v,i)=><option key={v.variant_id} value={v.variant_id}>{i+1}: {v.stops[0]?.name} → {v.stops.at(-1)?.name} ({v.stops.length} stops)</option>)}</select></label>
    {chosen&&<p>{chosen.scheduled_departures} scheduled trips · {chosen.frequency_windows} non-exact windows.<br/>
      Median departure interval at this variant’s first stop: {chosen.median_interval_minutes===null?'unavailable':`${chosen.median_interval_minutes.toFixed(1)} min`}</p>}
    <div className="section-heading"><span>Exact departures — click for stops</span></div>
    {pageRequest.error&&<p role="alert">Departures unavailable: {pageRequest.error}</p>}
    {filtered&&!base&&!pageRequest.error&&<p role="status">Loading matching departures…</p>}
    {last?.truncated&&<p className="warning">Showing {trips.length} of {matching} matching departures. Summary counts cover the full route.</p>}
    <div className="departure-list">{trips.map((t,i)=><button type="button" className="departure" data-trip-id={t.trip_id} data-departure-seconds={t.departure_seconds} data-variant-id={t.variant_id} key={t.trip_id+i} onClick={()=>{setTripId(t.trip_id);setInstance(t)}}>
      <strong>{clock(t.departure_local)}</strong><span>{t.first_stop_name} → {t.last_stop_name}<small>{t.headsign} · direction {t.direction_id??'unspecified'}</small>
       <small>Arrives {clock(t.arrival_local)} · {t.departure_local.slice(0,10)}</small></span></button>)}</div>
    {last?.truncated&&last.next_offset!=null&&<button type="button" className="load-more" disabled={pageLoading} onClick={()=>setWanted({key:pageKey,offset:last.next_offset!})}>{pageLoading?'Loading more departures…':'Load more departures'}</button>}
    {windows.length>0&&<><div className="section-heading"><span>Frequency windows — no exact times</span></div>{windows.map((w,i)=><p className="freq-window" key={i}>{clock(w.start_local)}–{clock(w.end_local)} · every {w.headway_seconds/60} min · {w.headsign}</p>)}</>}
    {timetable.windows_truncated&&<p className="warning">Frequency window results are truncated.</p>}
    {base&&!trips.length&&!windows.length&&<p>No matching departures on this date.</p>}
    <details><summary>Operating calendars and exceptions</summary>{timetable.calendars.map(c=><p key={String(c.service_id)}>{c.service_id}: {c.start_date}–{c.end_date}<br/>{['monday','tuesday','wednesday','thursday','friday','saturday','sunday'].filter(d=>c[d]===1).join(', ')||'No recurring weekdays'}</p>)}
     {timetable.exceptions.map((e,i)=><p key={i}>{e.service_date} · {e.service_id} · {e.exception_type===1?'added':'removed'}</p>)}</details>
    {!!timetable.warnings.length&&<details><summary>Feed warnings</summary>{timetable.warnings.map(w=><p key={w.issue_code}>{w.issue_code}: {w.count}</p>)}</details>}
    {timetable.provenance.landing_url&&<p><a href={timetable.provenance.landing_url} target="_blank" rel="noreferrer">Official source page</a></p>}
   </>}
   {busy&&<p role="status">Loading ordered stops and geometry…</p>}{error&&<p role="alert">{error}</p>}
   {trip&&<><div className="section-heading"><span>Ordered stops · {trip.trip.trip_id}</span></div>
    <p className="muted">{instance?'GTFS service-day times, including hours beyond 24:00.':'Template times only; select an exact departure to inspect an operating instance.'} {!trip.geometry.features.length?'No usable geometry was supplied.':trip.geometry.features[0]?.properties.quality==='schematic-stop-link'?'Dashed geometry is schematic.':'Geometry from the source feed.'}</p>
    <ol className="trip-stops" data-trip-id={trip.trip.trip_id} data-direction={direction} data-variant-id={variant}>{trip.stops.map(s=><li className="trip-stop" data-stop-id={s.stop_id} data-arrival={gtfs(s.arrival_sec,offset)} data-departure={gtfs(s.departure_sec,offset)} key={s.stop_sequence}><button type="button" onClick={()=>onStop({snapshot_id:route.snapshot_id,source_id:route.source_id,...s})}>{s.name||s.stop_id}</button><small>Arr {gtfs(s.arrival_sec,offset)} · Dep {gtfs(s.departure_sec,offset)}{s.timepoint===0?' · estimated timepoint':''}{s.pickup_type===1?' · no boarding':''}{s.drop_off_type===1?' · no alighting':''}</small></li>)}</ol></>}
  </>:stop?<><div className="inspect-label">STOP</div><h2>{stop.name||stop.stop_id}</h2><p>{stop.source_id} · {stop.stop_id}</p><Provenance snapshot={snapshot}/>
   {boardError?<p role="alert">Stop board unavailable: {boardError}</p>:!board?<p role="status">Loading stop departures…</p>:<><p>{board.departures.length} exact departures on local date {date}</p><div className="departure-list">{board.departures.map((d,i)=><div className="departure" key={i}><strong>{clock(d.local_departure)}</strong><span>{d.route_short_name} · {d.headsign}<small>Service day {d.service_date} · GTFS {d.gtfs_time}{!d.boarding_allowed?' · no boarding':''}</small></span></div>)}</div>
    {board.frequency_windows.map((w,i)=><p className="freq-window" key={i}>{clock(w.start_local)}–{clock(w.end_local)} · every ~{w.headway_seconds/60} min (not exact)</p>)}
    {!board.departures.length&&!board.frequency_windows.length&&<p>No departures reported for this date.</p>}</>}
  </>:<div className="inspector-empty"><strong>Select a route or stop</strong><span>Click the map or search the imported data to inspect schedules and provenance.</span></div>}</>;
}
