import React, {useEffect, useMemo, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import maplibregl, {GeoJSONSource, Map as MlMap} from 'maplibre-gl';
import 'maplibre-gl/dist/maplibre-gl.css';
import type { Feature, FeatureCollection } from 'geojson';
import {MapPinned, TrainFront, CalendarDays, Database, Route as RouteIcon, Search, Layers3, AlertTriangle, Clock3, ChevronRight} from 'lucide-react';
import {Departure, endpoints, FreqWindow, getJSON, Route, Snapshot, Stop, StopBoard, Summary} from './api';
import './styles.css';

function todayISO(){const d=new Date();return `${d.getFullYear()}-${`${d.getMonth()+1}`.padStart(2,'0')}-${`${d.getDate()}`.padStart(2,'0')}`}
const color=(n:number)=>n===2?'#d98937':n===0||n===1||n===5?'#a968ba':n===4?'#269c92':'#3c82c6';
const emptyGeoJSON=():FeatureCollection=>({type:'FeatureCollection',features:[]});
function App(){
 const [date,setDate]=useState(todayISO());
 const [routes,setRoutes]=useState<Route[]>([]),[stops,setStops]=useState<Stop[]>([]),[summary,setSummary]=useState<Summary|null>(null),[snapshots,setSnapshots]=useState<Snapshot[]>([]);
 const [mode,setMode]=useState('all'),[search,setSearch]=useState(''),[activeOnly,setActiveOnly]=useState(false);
 const [selectedRoute,setSelectedRoute]=useState<Route|null>(null),[selectedStop,setSelectedStop]=useState<Stop|null>(null);
 const [board,setBoard]=useState<StopBoard|null>(null),[error,setError]=useState<string|null>(null),[loading,setLoading]=useState(false);
 const mapEl=useRef<HTMLDivElement>(null),map=useRef<MlMap|null>(null);
 const abortCount=useRef(0);
 useEffect(()=>{if(!mapEl.current||map.current)return;
   const optionalBasemap=import.meta.env.VITE_BASEMAP_STYLE_URL as string|undefined;
   const blank:maplibregl.StyleSpecification={version:8,sources:{},layers:[{id:'background',type:'background',paint:{'background-color':'#eef3f0'}}]};
   const m=new maplibregl.Map({container:mapEl.current,style:optionalBasemap||blank,center:[9.13,40.0],zoom:7});map.current=m;
   m.addControl(new maplibregl.NavigationControl(), 'top-right');
   m.on('load',()=>{
     m.addSource('geometry',{type:'geojson',data:emptyGeoJSON()});
     m.addLayer({id:'route-lines',type:'line',source:'geometry',paint:{'line-color':['match',['get','quality'],'source-shape','#3a7ab4','#d98237'],'line-width':4,'line-opacity':.92,'line-dasharray':['match',['get','quality'],'source-shape',['literal',[1,0]],['literal',[2,1]]]}});
     m.addSource('stops',{type:'geojson',data:emptyGeoJSON()});
     m.addLayer({id:'stop-circles',type:'circle',source:'stops',paint:{'circle-radius':['interpolate',['linear'],['zoom'],5,2,10,5],'circle-color':'#f9fcff','circle-stroke-width':2,'circle-stroke-color':'#2d647e'}});
     m.on('click','stop-circles',(evt)=>{const f=evt.features?.[0]; if(f?.properties){const st=JSON.parse(f.properties['detail']) as Stop;setSelectedStop(st);setSelectedRoute(null)}});
     m.on('mouseenter','stop-circles',()=>m.getCanvas().style.cursor='pointer');
     m.on('mouseleave','stop-circles',()=>m.getCanvas().style.cursor='');
   });
   return ()=>{map.current=null;m.remove()}
 },[]);
 useEffect(()=>{
   const request=++abortCount.current;setLoading(true);setError(null);
   Promise.all([getJSON<Snapshot[]>(endpoints.snapshots),getJSON<Summary>(endpoints.summary(date)),getJSON<{routes:Route[]}>(endpoints.routes(date)),getJSON<{stops:Stop[]}>(endpoints.stops)])
      .then(([sn,sum,rr,ss])=>{if(request!==abortCount.current)return;setSnapshots(sn);setSummary(sum);setRoutes(rr.routes);setStops(ss.stops);setLoading(false)})
      .catch(e=>{if(request!==abortCount.current)return;setError(String(e));setLoading(false)});
 },[date]);
 useEffect(()=>{if(!map.current)return;const m=map.current;const push=()=>{
    const features:Feature[] = stops.filter(s=>Number.isFinite(s.lat)&&Number.isFinite(s.lon)).map(s=>({type:'Feature',properties:{detail:JSON.stringify(s)},geometry:{type:'Point',coordinates:[s.lon,s.lat]}}));
    (m.getSource('stops') as GeoJSONSource|undefined)?.setData({type:'FeatureCollection',features})
 };if(m.isStyleLoaded())push();else m.once('load',push)
 },[stops]);
 useEffect(()=>{if(!map.current)return;const m=map.current;let cancelled=false;
   const apply=(data:FeatureCollection)=>{
     if(cancelled)return;
     const src=m.getSource('geometry') as GeoJSONSource|undefined;src?.setData(data);
     if(data.features.length){const points:number[][]=[];for(const f of data.features){if(f.geometry.type==='LineString'){points.push(...f.geometry.coordinates as number[][])}}
       if(points.length){const bounds=new maplibregl.LngLatBounds();points.forEach(p=>bounds.extend([p[0],p[1]]));m.fitBounds(bounds,{padding:65,maxZoom:11,duration:600})}}
   };
   if(!selectedRoute){apply(emptyGeoJSON());return}
   getJSON<FeatureCollection>(endpoints.geometry(selectedRoute)).then(apply).catch(e=>{if(!cancelled)setError(String(e))});
   return ()=>{cancelled=true}
 },[selectedRoute]);
 useEffect(()=>{if(!selectedStop){setBoard(null);return}let cancelled=false;getJSON<StopBoard>(endpoints.board(selectedStop,date)).then(b=>{if(!cancelled)setBoard(b)}).catch(e=>{if(!cancelled)setError(String(e))});return ()=>{cancelled=true}},[selectedStop,date]);
 const shown=useMemo(()=>routes.filter(r=>(mode==='all'||(mode==='bus'?r.route_type===3:mode==='rail'?[0,1,2,5,12].includes(r.route_type):r.route_type===4))&&(!activeOnly||r.active_on_date)&&(`${r.short_name||''} ${r.long_name||''} ${r.source_id}`).toLowerCase().includes(search.toLowerCase())).slice(0,900),[routes,mode,activeOnly,search]);
 const counts=summary?.snapshots.reduce((a,s)=>({routes:a.routes+s.active_route_records,stops:a.stops+s.total_stop_records,trips:a.trips+s.scheduled_trip_instances,uncertain:a.uncertain+s.non_exact_frequency_windows}),{routes:0,stops:0,trips:0,uncertain:0});
 return <div className="app">
  <header><div className="brand"><TrainFront size={23}/><strong>Transport Atlas</strong><span className="brand-sub">Research workspace</span></div><div className="top-controls"><span className="region"><MapPinned size={16}/> Sardinia, Italy</span><label className="date"><CalendarDays size={16}/><input aria-label="Service date" type="date" value={date} onChange={e=>setDate(e.target.value)}/></label><span className="local-tag">LOCAL</span></div></header>
  <div className="workspace">
   <aside className="sidebar"><div className="sidebar-heading"><Layers3 size={17}/> Network explorer</div><label className="search"><Search size={16}/><input aria-label="Search routes" placeholder="Find route or operator..." value={search} onChange={e=>setSearch(e.target.value)}/></label>
    <div className="category"><span className="field-label">TRANSPORT MODE</span><div className="mode-tabs">{[['all','All'],['bus','Bus'],['rail','Rail'],['ferry','Ferry']].map(([v,label])=><button key={v} className={mode===v?'active':''} onClick={()=>setMode(v)}>{label}</button>)}</div></div>
    <label className="check"><input type="checkbox" checked={activeOnly} onChange={e=>setActiveOnly(e.target.checked)}/> Operating on selected date only</label>
    <div className="section-heading"><span>ROUTES</span><span>{shown.length}{shown.length===900?'+':''}</span></div>
    <div className="route-list">{shown.length?shown.map(r=><button className={'route-item '+(selectedRoute?.route_id===r.route_id&&selectedRoute.snapshot_id===r.snapshot_id?'selected':'')} key={`${r.snapshot_id}/${r.route_id}`} onClick={()=>{setSelectedRoute(r);setSelectedStop(null)}}><span className="route-square" style={{background:color(r.route_type)}}/><span className="route-label"><strong>{r.short_name||r.route_id}</strong><small title={r.long_name}>{r.long_name||'Untitled route'}</small><em>{r.source_id}</em></span><span className={'status-dot '+(r.active_on_date?'on':'off')} title={r.active_on_date?'Active':'Inactive on date'}/></button>):<p className="empty">No matching routes. Import a GTFS ZIP or adjust filters.</p>}</div>
    <footer className="sidebar-footer"><Database size={14}/> {snapshots.length} selected source snapshots</footer>
   </aside>
   <main className="main-panel"><div className="metrics"><div><span>OPERATING ROUTES</span><strong>{counts?.routes??'—'}</strong></div><div><span>STOP RECORDS</span><strong>{counts?.stops??'—'}</strong></div><div><span>SCHEDULED TRIPS</span><strong>{counts?.trips??'—'}</strong></div><div><span>NON-EXACT WINDOWS</span><strong>{counts?.uncertain??'—'}</strong></div></div>
     {summary?.snapshots.some(s=>!s.service_date_covered)&&<div className="warning"><AlertTriangle size={16}/> At least one feed does not cover this selected service date. Missing service must not be interpreted as confirmed absence.</div>}
     {error&&<div className="warning"><AlertTriangle size={16}/> {error}</div>}
     <div className="map-container"><div ref={mapEl} className="map"/><div className="map-overlay"><strong>Routes & stops</strong><span>{selectedRoute?'Selected geometry':'Select a route to display its alignment'}</span></div><div className="legend"><span><i style={{background:'#3a7ab4'}}/>Source geometry</span><span><i style={{background:'#d98237'}}/>Schematic links</span></div></div>
     <div className="bottom"><div className="bottom-title"><Clock3 size={16}/> Service coverage by source</div><div className="source-strip">{summary?.snapshots.map(s=><div className="source-row" key={s.snapshot_id}><strong>{s.source_id}</strong><span>{s.active_route_records} active routes</span><span>{s.scheduled_trip_instances} scheduled trips</span><small>{s.feed_start}–{s.feed_end}</small></div>)}{!summary?.snapshots.length&&<span className="muted">No imported feeds yet. See README for the import command.</span>}</div></div>
   </main>
   <aside className="inspector"><div className="inspector-heading">INSPECTOR</div>{selectedRoute?<><div className="inspect-label">ROUTE</div><h2>{selectedRoute.short_name||selectedRoute.route_id}</h2><p>{selectedRoute.long_name}</p><div className="inspect-line"><span>Operator source</span><strong>{selectedRoute.source_id}</strong></div><div className="inspect-line"><span>Selected date</span><strong>{selectedRoute.active_on_date?'Active':'Not active'}</strong></div><div className="inspect-line"><span>Route type</span><strong>{selectedRoute.route_type}</strong></div><p className="muted">This initial layer shows all geometry variants together. Pattern-level comparisons are a subsequent milestone.</p></>
   :selectedStop?<><div className="inspect-label">STOP</div><h2>{selectedStop.name||selectedStop.stop_id}</h2><p className="muted">{selectedStop.stop_id} · {selectedStop.lat?.toFixed(4)}, {selectedStop.lon?.toFixed(4)}</p><div className="section-heading"><span>Scheduled departures</span><span>{board?.departures.length??'…'}</span></div><div className="departure-list">{board?.departures.map((d:Departure,i)=><div className="departure" key={d.trip_id+i}><strong>{d.local_departure.slice(11,16)}</strong><span><b>{d.route_short_name}</b> {d.headsign}<small>Service day: {d.service_date}</small></span></div>)}</div>{(board?.frequency_windows.length??0)>0&&<><div className="section-heading"><span>Headway services</span></div>{board?.frequency_windows.map((w:FreqWindow,i)=><p key={i} className="freq-window">{w.start_local.slice(11,16)}–{w.end_local.slice(11,16)} · every ~{Math.round(w.headway_seconds/60)} min (not exact)</p>)}</>}</>
   :<div className="inspector-empty"><RouteIcon size={28}/><strong>Select a route or stop</strong><span>Inspect source identity, service dates, geometry, and scheduled departures.</span><ChevronRight size={19}/></div>}
   </aside>
  </div><div className="app-status">{loading?'Loading local datasets…':'Source data: imported GTFS snapshots'} · Timetable information is subject to feed validity and completeness.</div>
 </div>
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
