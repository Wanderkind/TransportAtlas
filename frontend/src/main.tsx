import React, {useEffect, useMemo, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import maplibregl, {GeoJSONSource, Map as MlMap} from 'maplibre-gl';
import type {FeatureCollection} from 'geojson';
import 'maplibre-gl/dist/maplibre-gl.css';
import {MapPinned, TrainFront, CalendarDays, Database, Route as RouteIcon, Search,
        Layers3, AlertTriangle, Clock3, Map as MapIcon, Maximize2, WifiOff} from 'lucide-react';
import {Departure, endpoints, FreqWindow, getJSON, NetworkGeometry, Route,
        RouteTimetable, Snapshot, Stop, StopBoard, Summary} from './api';
import './styles.css';

const STREETS_STYLE='https://tiles.openfreemap.org/styles/liberty';
const PLAIN_STYLE:maplibregl.StyleSpecification={version:8,sources:{},layers:[{id:'background',type:'background',paint:{'background-color':'#eef3f0'}}]};
function todayISO(){const d=new Date();return `${d.getFullYear()}-${`${d.getMonth()+1}`.padStart(2,'0')}-${`${d.getDate()}`.padStart(2,'0')}`}
function routeColor(n:number){return [0,1,2,5,12].includes(n)?'#b36b9e':n===4?'#229a90':[3,11].includes(n)?'#3583be':'#bd863b'}
const emptyGeoJSON=():FeatureCollection=>({type:'FeatureCollection',features:[]});
const emptyNetwork=():NetworkGeometry=>({type:'FeatureCollection',features:[],metadata:{service_date:'',active_only:true,source_count:0,route_count:0,feature_count:0,point_count:0,truncated:false,missing_geometry_routes:0,warning:null}});
const key=(r:{snapshot_id:string;route_id:string})=>`${r.snapshot_id}:${r.route_id}`;
const clock=(s:string|null|undefined)=>s?.slice(11,16)||'—';

function ensureLayers(m:MlMap){
 if(!m.getSource('network'))m.addSource('network',{type:'geojson',data:emptyGeoJSON()});
 if(!m.getLayer('route-network'))m.addLayer({id:'route-network',type:'line',source:'network',
    filter:['==',['get','quality'],'source-shape'],
    paint:{'line-color':['get','color'],'line-width':['interpolate',['linear'],['zoom'],5,1.3,9,3],
           'line-opacity':0.66}});
 if(!m.getLayer('route-schematic'))m.addLayer({id:'route-schematic',type:'line',source:'network',
    filter:['==',['get','quality'],'schematic-stop-link'],
    paint:{'line-color':['get','color'],'line-width':2.5,'line-opacity':0.65,'line-dasharray':[3,2]}});
 if(!m.getLayer('route-highlight'))m.addLayer({id:'route-highlight',type:'line',source:'network',
    filter:['==',['get','uid'],'__none__'],
    paint:{'line-color':'#ffb53b','line-width':6,'line-opacity':0.96}});
 if(!m.getSource('stops'))m.addSource('stops',{type:'geojson',data:emptyGeoJSON()});
 if(!m.getLayer('stop-circles'))m.addLayer({id:'stop-circles',type:'circle',source:'stops',
    paint:{'circle-radius':['interpolate',['linear'],['zoom'],5,2,10,4],
           'circle-color':'#f9fcff','circle-stroke-width':1.4,'circle-stroke-color':'#1d6489'}});
}
function showNetwork(m:MlMap,net:NetworkGeometry,stops:Stop[],selected:Route|null){
 if(!m.getSource('network'))return;
 (m.getSource('network') as GeoJSONSource).setData({type:'FeatureCollection',features:net.features} as FeatureCollection);
 const pointFeatures=stops.filter(s=>Number.isFinite(s.lat)&&Number.isFinite(s.lon)).map(s=>({
   type:'Feature' as const,properties:{snapshot_id:s.snapshot_id,stop_id:s.stop_id},
   geometry:{type:'Point' as const,coordinates:[s.lon,s.lat]}}));
 (m.getSource('stops') as GeoJSONSource).setData({type:'FeatureCollection',features:pointFeatures});
 if(m.getLayer('route-highlight'))m.setFilter('route-highlight', ['==',['get','uid'],selected?key(selected):'__none__']);
 if(m.getLayer('route-network'))m.setPaintProperty('route-network','line-opacity',selected?0.24:0.66);
 if(m.getLayer('route-schematic'))m.setPaintProperty('route-schematic','line-opacity',selected?0.24:0.65);
}
function fitNetwork(m:MlMap,net:NetworkGeometry,stops:Stop[]){
 const bounds=new maplibregl.LngLatBounds();
 for(const f of net.features)for(const p of f.geometry.coordinates){if(Number.isFinite(p[0])&&Number.isFinite(p[1]))bounds.extend([p[0],p[1]])}
 if(bounds.isEmpty())for(const stop of stops){if(Number.isFinite(stop.lat)&&Number.isFinite(stop.lon))bounds.extend([stop.lon,stop.lat])}
 if(!bounds.isEmpty())m.fitBounds(bounds,{padding:55,maxZoom:11,duration:500});
}

function App(){
 const [date,setDate]=useState(todayISO());
 const [routes,setRoutes]=useState<Route[]>([]),[stops,setStops]=useState<Stop[]>([]),[summary,setSummary]=useState<Summary|null>(null),[snapshots,setSnapshots]=useState<Snapshot[]>([]);
 const [mode,setMode]=useState('all'),[source,setSource]=useState(''),[search,setSearch]=useState(''),[activeOnly,setActiveOnly]=useState(true);
 const [selectedRoute,setSelectedRoute]=useState<Route|null>(null),[selectedStop,setSelectedStop]=useState<Stop|null>(null);
 const [board,setBoard]=useState<StopBoard|null>(null),[timetable,setTimetable]=useState<RouteTimetable|null>(null),[direction,setDirection]=useState('all');
 const [network,setNetwork]=useState<NetworkGeometry>(emptyNetwork()),[networkLoading,setNetworkLoading]=useState(false);
 const [basemap,setBasemap]=useState<'streets'|'plain'>('streets'),[basemapWarning,setBasemapWarning]=useState('');
 const [error,setError]=useState<string|null>(null),[loading,setLoading]=useState(false);
 const mapEl=useRef<HTMLDivElement>(null),map=useRef<MlMap|null>(null),hasFit=useRef(false),previousStyle=useRef('streets');
 const networkRef=useRef(network),stopsRef=useRef(stops),routesRef=useRef(routes),selectedRef=useRef(selectedRoute);
 networkRef.current=network;stopsRef.current=stops;routesRef.current=routes;selectedRef.current=selectedRoute;

 useEffect(()=>{
   if(!mapEl.current||map.current)return;
   const m=new maplibregl.Map({container:mapEl.current,style:STREETS_STYLE,center:[9.13,40.0],zoom:7});map.current=m;
   m.addControl(new maplibregl.NavigationControl(),'top-right');
   const update=()=>{
     ensureLayers(m);showNetwork(m,networkRef.current,stopsRef.current,selectedRef.current);
     if(!hasFit.current && (networkRef.current.features.length||stopsRef.current.length)){
       fitNetwork(m,networkRef.current,stopsRef.current);hasFit.current=true;
     }
   };
   m.on('style.load',update);
   m.on('error',()=>{
     // Basemap failures must not prevent local route rendering.
     if(previousStyle.current==='streets'){
       setBasemapWarning('Online basemap unavailable; switched to the plain offline map.');
       setBasemap('plain');
     }
   });
   const openRoute=(evt:maplibregl.MapLayerMouseEvent)=>{
     const properties=evt.features?.[0]?.properties;
     if(!properties)return;
     const route=routesRef.current.find(r=>r.snapshot_id===properties.snapshot_id&&r.route_id===properties.route_id);
     if(route){setSelectedRoute(route);setSelectedStop(null)}
   };
   for(const layer of ['route-network','route-schematic','route-highlight'])m.on('click',layer,openRoute);
   m.on('click','stop-circles',evt=>{
     const p=evt.features?.[0]?.properties;
     const stop=stopsRef.current.find(s=>s.snapshot_id===p?.snapshot_id&&s.stop_id===p?.stop_id);
     if(stop){setSelectedStop(stop);setSelectedRoute(null)}
   });
   for(const layer of ['route-network','route-schematic','stop-circles']){
     m.on('mouseenter',layer,()=>{m.getCanvas().style.cursor='pointer'});
     m.on('mouseleave',layer,()=>{m.getCanvas().style.cursor=''})
   }
   return ()=>{map.current=null;m.remove()};
 },[]);
 useEffect(()=>{
   if(!map.current||previousStyle.current===basemap)return;
   previousStyle.current=basemap;
   map.current.setStyle(basemap==='streets'?STREETS_STYLE:PLAIN_STYLE);
 },[basemap]);
 useEffect(()=>{
   const m=map.current;
   if(!m||!m.getSource('network'))return;
   showNetwork(m,network,stops,selectedRoute);
   if(!hasFit.current && (network.features.length||stops.length)){
     fitNetwork(m,network,stops);hasFit.current=true;
   }
 },[network,stops,selectedRoute]);
 useEffect(()=>{
   const controller=new AbortController();setLoading(true);setError(null);
   Promise.all([getJSON<Snapshot[]>(endpoints.snapshots,controller.signal),getJSON<Summary>(endpoints.summary(date),controller.signal),
       getJSON<{routes:Route[]}>(endpoints.routes(date),controller.signal),getJSON<{stops:Stop[]}>(endpoints.stops,controller.signal)])
       .then(([sn,sum,rr,ss])=>{if(controller.signal.aborted)return;setSnapshots(sn);setSummary(sum);setRoutes(rr.routes);setStops(ss.stops);setLoading(false)})
       .catch(e=>{if(!controller.signal.aborted){setError(String(e));setLoading(false)}});
   return ()=>controller.abort();
 },[date]);
 useEffect(()=>{
   const controller=new AbortController();setNetworkLoading(true);
   getJSON<NetworkGeometry>(endpoints.network(date,mode,source,activeOnly),controller.signal)
     .then(data=>{if(controller.signal.aborted)return;setNetwork(data);setNetworkLoading(false)})
     .catch(e=>{if(!controller.signal.aborted){setNetworkLoading(false);setError(`Network geometry: ${String(e)}`)}});
   return ()=>controller.abort();
 },[date,mode,source,activeOnly]);
 useEffect(()=>{
   if(!selectedStop){setBoard(null);return}
   const controller=new AbortController();setBoard(null);
   getJSON<StopBoard>(endpoints.board(selectedStop,date),controller.signal)
     .then(b=>{if(!controller.signal.aborted)setBoard(b)})
     .catch(e=>{if(!controller.signal.aborted)setError(String(e))});
   return ()=>controller.abort();
 },[selectedStop,date]);
 useEffect(()=>{
   if(!selectedRoute){setTimetable(null);return}
   const controller=new AbortController();setTimetable(null);setDirection('all');
   getJSON<RouteTimetable>(endpoints.timetable(selectedRoute,date),controller.signal)
     .then(data=>{if(!controller.signal.aborted)setTimetable(data)})
     .catch(e=>{if(!controller.signal.aborted)setError(String(e))});
   return ()=>controller.abort();
 },[selectedRoute,date]);
 useEffect(()=>{
   if(selectedRoute && (source&&selectedRoute.source_id!==source ||
       mode==='bus'&&![3,11].includes(selectedRoute.route_type) ||
       mode==='rail'&&![0,1,2,5,12].includes(selectedRoute.route_type) ||
       mode==='ferry'&&selectedRoute.route_type!==4 ||
       activeOnly&&!selectedRoute.active_on_date))setSelectedRoute(null);
 },[source,mode,activeOnly,selectedRoute]);
 const shown=useMemo(()=>routes.filter(r=>(!source||r.source_id===source)&&
   (mode==='all'||(mode==='bus'?[3,11].includes(r.route_type):mode==='rail'?[0,1,2,5,12].includes(r.route_type):r.route_type===4))&&
   (!activeOnly||r.active_on_date)&&(`${r.short_name||''} ${r.long_name||''} ${r.source_id}`).toLowerCase().includes(search.toLowerCase())).slice(0,900),
   [routes,source,mode,activeOnly,search]);
 const counts=summary?.snapshots.filter(s=>!source||s.source_id===source).reduce((a,s)=>({
   routes:a.routes+s.active_route_records,stops:a.stops+s.total_stop_records,trips:a.trips+s.scheduled_trip_instances,
   uncertain:a.uncertain+s.non_exact_frequency_windows}),{routes:0,stops:0,trips:0,uncertain:0});
 const displayedTrips=timetable?.departures.filter(t=>direction==='all'||String(t.direction_id)===direction)??[];
 const displayedWindows=timetable?.frequency_windows.filter(t=>direction==='all'||String(t.direction_id)===direction)??[];
 return <div className="app">
  <header><div className="brand"><TrainFront size={23}/><strong>Transport Atlas</strong><span className="brand-sub">Research workspace</span></div>
   <div className="top-controls"><span className="region"><MapPinned size={16}/> Imported networks</span>
    <label className="date"><CalendarDays size={16}/><input aria-label="Service date" type="date" value={date} onChange={e=>setDate(e.target.value)}/></label>
    <span className="local-tag">LOCAL</span></div>
  </header>
  <div className="workspace">
   <aside className="sidebar"><div className="sidebar-heading"><Layers3 size={17}/> Network explorer</div>
    <label className="search"><Search size={16}/><input aria-label="Search routes" placeholder="Find route or operator..." value={search} onChange={e=>setSearch(e.target.value)}/></label>
    <div className="category"><span className="field-label">SOURCE / OPERATOR</span>
     <select className="source-select" aria-label="Data source" value={source} onChange={e=>setSource(e.target.value)}>
       <option value="">All imported sources</option>
       {snapshots.map(s=><option key={s.snapshot_id} value={s.source_id}>{s.source_id}</option>)}
     </select></div>
    <div className="category"><span className="field-label">TRANSPORT MODE</span><div className="mode-tabs">
     {([['all','All'],['bus','Bus'],['rail','Rail'],['ferry','Ferry']] as const).map(([v,label])=><button type="button" key={v} className={mode===v?'active':''} onClick={()=>setMode(v)}>{label}</button>)}</div></div>
    <label className="check"><input type="checkbox" checked={activeOnly} onChange={e=>setActiveOnly(e.target.checked)}/> Operating on selected date only</label>
    <div className="section-heading"><span>ROUTES</span><span>{shown.length}{shown.length===900?'+':''}</span></div>
    <div className="route-list">{shown.length?shown.map(r=><button type="button" className={'route-item '+(selectedRoute&&key(selectedRoute)===key(r)?'selected':'')}
      key={key(r)} onClick={()=>{setSelectedRoute(r);setSelectedStop(null)}}>
      <span className="route-square" style={{background:routeColor(r.route_type)}}/><span className="route-label"><strong>{r.short_name||r.route_id}</strong>
      <small title={r.long_name}>{r.long_name||'Untitled route'}</small><em>{r.source_id}</em></span>
      <span className={'status-dot '+(r.active_on_date?'on':'off')} title={r.active_on_date?'Active':'Inactive on date'}/></button>):<p className="empty">No matching routes. Import GTFS or adjust the filters.</p>}</div>
    <footer className="sidebar-footer"><Database size={14}/> {snapshots.length} selected source snapshots</footer>
   </aside>
   <main className="main-panel"><div className="metrics"><div><span>OPERATING ROUTES</span><strong>{counts?.routes??'—'}</strong></div>
       <div><span>STOP RECORDS</span><strong>{counts?.stops??'—'}</strong></div>
       <div><span>SCHEDULED TRIPS</span><strong>{counts?.trips??'—'}</strong></div>
       <div><span>NON-EXACT WINDOWS</span><strong>{counts?.uncertain??'—'}</strong></div></div>
     {summary?.snapshots.some(s=>(!source||source===s.source_id)&&!s.service_date_covered)&&<div className="warning"><AlertTriangle size={16}/> One or more imported feeds do not cover this date. Missing service is not confirmed absence.</div>}
     {network.metadata.warning&&<div className="warning"><AlertTriangle size={16}/> {network.metadata.warning}</div>}
     {basemapWarning&&<div className="warning"><WifiOff size={16}/> {basemapWarning}</div>}
     {error&&<div className="warning"><AlertTriangle size={16}/> {error}</div>}
     <div className="map-container"><div ref={mapEl} className="map"/>
       <div className="map-overlay"><strong>Geographic route atlas</strong><span>{networkLoading?'Loading network geometry…':`${network.metadata.route_count} routes · ${network.metadata.feature_count} alignments`}</span></div>
       <div className="map-actions"><button type="button" onClick={()=>{if(map.current)fitNetwork(map.current,network,stops)}} title="Fit routes and stops"><Maximize2 size={15}/> Fit</button>
        <button type="button" onClick={()=>{setBasemap(s=>s==='streets'?'plain':'streets');setBasemapWarning('')}} title="Toggle online street map">
         <MapIcon size={15}/>{basemap==='streets'?'Street map':'Plain map'}</button></div>
       <div className="legend"><span><i style={{background:'#3583be'}}/>Bus</span><span><i style={{background:'#b36b9e'}}/>Rail</span>
        <span><i style={{background:'#229a90'}}/>Ferry</span><span>Schematic links dashed</span></div>
     </div>
     <div className="bottom"><div className="bottom-title"><Clock3 size={16}/> Service coverage by source</div>
       <div className="source-strip">{summary?.snapshots.filter(s=>!source||s.source_id===source).map(s=><div className="source-row" key={s.snapshot_id}>
         <strong>{s.source_id}</strong><span>{s.active_route_records} active routes</span><span>{s.scheduled_trip_instances} scheduled trips</span>
         <small>{s.feed_start}–{s.feed_end}</small></div>)}{!summary?.snapshots.length&&<span className="muted">No imported feeds. See README for an import command.</span>}</div></div>
   </main>
   <aside className="inspector"><div className="inspector-heading">INSPECTOR</div>{selectedRoute?<>
       <div className="inspect-label">ROUTE</div><h2>{selectedRoute.short_name||selectedRoute.route_id}</h2><p>{selectedRoute.long_name}</p>
       <div className="inspect-line"><span>Source</span><strong>{selectedRoute.source_id}</strong></div>
       <div className="inspect-line"><span>Selected date</span><strong>{selectedRoute.active_on_date?'Active':'Not active'}</strong></div>
       <div className="inspect-line"><span>Mode type</span><strong>{selectedRoute.route_type}</strong></div>
       <div className="section-heading"><span>Scheduled departures</span><span>{timetable?.total_departures??'…'}</span></div>
       <div className="direction-picker"><label htmlFor="direction">Direction</label><select id="direction" value={direction} onChange={e=>setDirection(e.target.value)}>
         <option value="all">All directions</option><option value="0">Direction 0</option><option value="1">Direction 1</option><option value="null">Unspecified</option>
       </select></div>
       <div className="departure-list">{displayedTrips.map((t,i)=><div className="departure" key={t.trip_id+i}>
         <strong>{clock(t.departure_local)}</strong><span><b>{t.first_stop_name||'Unknown origin'} → {t.last_stop_name||'Unknown destination'}</b>
         <small>{t.headsign||''}{t.direction_id!==null?` · Dir ${t.direction_id}`:''}</small>
         <small>{t.arrival_local?`Arrives ${clock(t.arrival_local)}`:'Arrival unknown'} · {t.departure_local.slice(0,10)}</small></span></div>)}</div>
       {timetable?.truncated&&<p className="muted">Only the first 200 departures are displayed. Narrow the query for full results.</p>}
       {displayedWindows.length>0&&<><div className="section-heading"><span>Headway windows (not exact)</span></div>
         {displayedWindows.map((w,i)=><p className="freq-window" key={i}>{clock(w.start_local)}–{clock(w.end_local)} · every {Math.round(w.headway_seconds/60)} min · {w.first_stop_name}</p>)}</>}
       {timetable&&timetable.total_departures===0&&displayedWindows.length===0&&<p className="muted">No departures reported on this service date.</p>}
       <p className="muted">Schedules are GTFS service-date-based; trips after midnight may display the following calendar date.</p>
   </>:selectedStop?<><div className="inspect-label">STOP</div><h2>{selectedStop.name||selectedStop.stop_id}</h2>
       <p className="muted">{selectedStop.stop_id} · {selectedStop.lat?.toFixed(4)}, {selectedStop.lon?.toFixed(4)}</p>
       <div className="section-heading"><span>Scheduled departures</span><span>{board?.departures.length??'…'}</span></div>
       <div className="departure-list">{board?.departures.map((d:Departure,i)=><div className="departure" key={d.trip_id+i}>
        <strong>{clock(d.local_departure)}</strong><span><b>{d.route_short_name}</b> {d.headsign}<small>Service day: {d.service_date}</small></span></div>)}</div>
       {(board?.frequency_windows.length??0)>0&&<><div className="section-heading"><span>Headway services</span></div>
         {board?.frequency_windows.map((w:FreqWindow,i)=><p key={i} className="freq-window">{clock(w.start_local)}–{clock(w.end_local)} · every ~{Math.round(w.headway_seconds/60)} min (not exact)</p>)}</>}
   </>:<div className="inspector-empty"><RouteIcon size={28}/><strong>Select a route or stop</strong>
     <span>Click a line or stop on the map, or select a route from the list to inspect its services.</span></div>}</aside>
  </div><div className="app-status">{loading?'Loading dataset…':networkLoading?'Loading geometry…':`Showing ${network.metadata.feature_count} route alignments`} ·
    {basemap==='streets'?' Online basemap requires internet':' Offline plain map'} · Imported data may be incomplete or expired.</div>
 </div>
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
