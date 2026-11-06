import React, {useEffect, useMemo, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import * as maplibregl from 'maplibre-gl';
import {GeoJSONSource, Map as MlMap} from 'maplibre-gl';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import type {FeatureCollection} from 'geojson';
import 'maplibre-gl/dist/maplibre-gl.css';
import {MapPinned, TrainFront, CalendarDays, Database, Search,
        Layers3, AlertTriangle, Clock3, Map as MapIcon, Maximize2, WifiOff} from './icons';
import {endpoints, getJSON, NetworkGeometry, Route,
        RouteTimetable, Snapshot, Stop, StopBoard, Summary} from './api';
import './styles.css';
import {Inspector} from './Inspector';
maplibregl.setWorkerUrl(workerUrl);

const STREETS_STYLE='https://tiles.openfreemap.org/styles/liberty';
const PLAIN_STYLE:maplibregl.StyleSpecification={version:8,sources:{},layers:[{id:'background',type:'background',paint:{'background-color':'#eef3f0'}}]};
function todayISO(){const requested=new URLSearchParams(window.location.search).get('date');if(requested && /^\d{4}-\d{2}-\d{2}$/.test(requested))return requested;const d=new Date();return `${d.getFullYear()}-${`${d.getMonth()+1}`.padStart(2,'0')}-${`${d.getDate()}`.padStart(2,'0')}`}
function routeColor(n:number){return n===0?'#da7933':[1,2,5,12].includes(n)?'#b36b9e':n===4?'#229a90':[3,11].includes(n)?'#3583be':'#bd863b'}
const emptyGeoJSON=():FeatureCollection=>({type:'FeatureCollection',features:[]});
const emptyNetwork=():NetworkGeometry=>({type:'FeatureCollection',features:[],metadata:{service_date:'',active_only:true,source_count:0,route_count:0,feature_count:0,point_count:0,truncated:false,missing_geometry_routes:0,warning:null}});
const key=(r:{snapshot_id:string;route_id:string})=>`${r.snapshot_id}:${r.route_id}`;
const modeMatches=(n:number,m:string)=>m==='all'||(m==='tram'?n===0:m==='rail'?[1,2,5,12].includes(n):m==='bus'?[3,11].includes(n):n===4);
const sourceData=new WeakMap<GeoJSONSource,unknown>();
function updateSource(source:GeoJSONSource,data:FeatureCollection,identity:unknown){if(sourceData.get(source)!==identity){source.setData(data);sourceData.set(source,identity)}}

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
 if(!m.getLayer('route-highlight-schematic'))m.addLayer({id:'route-highlight-schematic',type:'line',source:'network',filter:['==',['get','uid'],'__none__'],paint:{'line-color':'#ffb53b','line-width':6,'line-opacity':0.96,'line-dasharray':[3,2]}});
 if(!m.getSource('focused'))m.addSource('focused',{type:'geojson',data:emptyGeoJSON()});
 if(!m.getLayer('focused-line'))m.addLayer({id:'focused-line',type:'line',source:'focused',filter:['==',['get','quality'],'source-shape'],paint:{'line-color':'#ffb53b','line-width':7}});
 if(!m.getLayer('focused-schematic'))m.addLayer({id:'focused-schematic',type:'line',source:'focused',filter:['==',['get','quality'],'schematic-stop-link'],paint:{'line-color':'#ffb53b','line-width':7,'line-dasharray':[3,2]}});
 if(!m.getSource('stops'))m.addSource('stops',{type:'geojson',data:emptyGeoJSON(),cluster:true,clusterRadius:35,clusterMaxZoom:10});
 if(!m.getLayer('stop-clusters'))m.addLayer({id:'stop-clusters',type:'circle',source:'stops',filter:['has','point_count'],paint:{'circle-radius':13,'circle-color':'#1d6489','circle-opacity':0.7}});
 if(!m.getLayer('stop-circles'))m.addLayer({id:'stop-circles',type:'circle',source:'stops',filter:['!', ['has','point_count']],
    paint:{'circle-radius':['interpolate',['linear'],['zoom'],5,2,10,4],
           'circle-color':'#f9fcff','circle-stroke-width':1.4,'circle-stroke-color':'#1d6489'}});
}
function showNetwork(m:MlMap,net:NetworkGeometry,stops:Stop[],selected:Route|null,focused:NetworkGeometry|null,direction:string){
 if(!m.getSource('network'))return;
 updateSource(m.getSource('network') as GeoJSONSource,{type:'FeatureCollection',features:net.features} as FeatureCollection,net);
 updateSource(m.getSource('focused') as GeoJSONSource,(focused??emptyGeoJSON()) as FeatureCollection,focused);
 const pointFeatures=stops.filter(s=>Number.isFinite(s.lat)&&Number.isFinite(s.lon)).map(s=>({
   type:'Feature' as const,properties:{snapshot_id:s.snapshot_id,stop_id:s.stop_id},
   geometry:{type:'Point' as const,coordinates:[s.lon,s.lat]}}));
 updateSource(m.getSource('stops') as GeoJSONSource,{type:'FeatureCollection',features:pointFeatures},stops);
 for(const [layer,quality] of [['route-highlight','source-shape'],['route-highlight-schematic','schematic-stop-link']]){
   if(m.getLayer(layer))m.setFilter(layer, ['all',['==',['get','uid'],selected&&!focused?key(selected):'__none__'],['==',['get','quality'],quality],direction==='all'?true:['in',direction,['get','direction_ids']]]);
 }
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
 const [board,setBoard]=useState<StopBoard|null>(null),[timetable,setTimetable]=useState<RouteTimetable|null>(null);
 const [focused,setFocused]=useState<NetworkGeometry|null>(null),[hiddenSources,setHiddenSources]=useState<string[]>([]),[stopSearch,setStopSearch]=useState(''),[listWarning,setListWarning]=useState('');
 const [network,setNetwork]=useState<NetworkGeometry>(emptyNetwork()),[networkLoading,setNetworkLoading]=useState(false);
 const [basemap,setBasemap]=useState<'streets'|'plain'>(new URLSearchParams(location.search).get('basemap')==='plain'?'plain':'streets'),[basemapWarning,setBasemapWarning]=useState('');
 const [error,setError]=useState<string|null>(null),[loading,setLoading]=useState(false);
 const [stopWarning,setStopWarning]=useState(''),[searchStops,setSearchStops]=useState<Stop[]>([]),[stopLoading,setStopLoading]=useState(false),[searching,setSearching]=useState(false);
 const [catalogLoaded,setCatalogLoaded]=useState(false);
 const [selectedDirection,setSelectedDirection]=useState('all');
 const directionRef=useRef(selectedDirection);directionRef.current=selectedDirection;
 const sourceIds=catalogLoaded?snapshots.filter(s=>(!source||s.source_id===source)&&!hiddenSources.includes(s.source_id)).map(s=>s.source_id).join(','):undefined;
 const mapEl=useRef<HTMLDivElement>(null),map=useRef<MlMap|null>(null),hasFit=useRef(false),previousStyle=useRef(basemap);
 const visibleNetwork=useMemo(()=>({...network,features:network.features.filter(f=>(!source||source===f.properties.source_id)&&!hiddenSources.includes(f.properties.source_id))}),[network,hiddenSources,source]);
 const visibleStops=useMemo(()=>stops.filter(s=>(!source||source===s.source_id)&&!hiddenSources.includes(s.source_id)),[stops,source,hiddenSources]);
 const focusedRef=useRef(focused);focusedRef.current=focused;
 const networkRef=useRef(visibleNetwork),stopsRef=useRef(stops),routesRef=useRef(routes),selectedRef=useRef(selectedRoute);
 networkRef.current=visibleNetwork;stopsRef.current=visibleStops;routesRef.current=routes;selectedRef.current=selectedRoute;

 useEffect(()=>{
   if(!mapEl.current||map.current)return;
   const m=new maplibregl.Map({container:mapEl.current,style:basemap==='plain'?PLAIN_STYLE:STREETS_STYLE,center:[9.13,40.0],zoom:7});map.current=m;
   m.addControl(new maplibregl.NavigationControl(),'top-right');
   const update=()=>{
     ensureLayers(m);showNetwork(m,networkRef.current,stopsRef.current,selectedRef.current,focusedRef.current,directionRef.current);
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
   for(const layer of ['route-network','route-schematic','route-highlight','route-highlight-schematic'])m.on('click',layer,openRoute);
   m.on('click','stop-clusters',evt=>{const f=evt.features?.[0];if(f?.geometry.type==='Point')m.easeTo({center:f.geometry.coordinates as [number,number],zoom:Math.min(m.getZoom()+2,12)})});
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
   showNetwork(m,visibleNetwork,visibleStops,selectedRoute,focused,selectedDirection);
   if(!hasFit.current && (network.features.length||stops.length)){
     fitNetwork(m,visibleNetwork,visibleStops);hasFit.current=true;
   }
 },[visibleNetwork,visibleStops,selectedRoute,focused,selectedDirection]);
 useEffect(()=>{
   const controller=new AbortController();setLoading(true);setError(null);
   Promise.all([getJSON<Snapshot[]>(endpoints.snapshots,controller.signal),getJSON<Summary>(endpoints.summary(date),controller.signal),
       getJSON<{routes:Route[];warning:string|null}>(endpoints.routes(date),controller.signal)])
       .then(([sn,sum,rr])=>{if(controller.signal.aborted)return;setSnapshots(sn);setCatalogLoaded(true);setSummary(sum);setRoutes(rr.routes);setListWarning(rr.warning??'');setSelectedRoute(current=>current?rr.routes.find(r=>key(r)===key(current))??null:null);setLoading(false)})
       .catch(e=>{if(!controller.signal.aborted){setError(String(e));setLoading(false)}});
   return ()=>controller.abort();
 },[date]);
 useEffect(()=>{
   if(!catalogLoaded)return;
   const controller=new AbortController();setStopLoading(true);
   getJSON<{stops:Stop[];warning:string|null}>(endpoints.stops(date,mode,activeOnly,sourceIds),controller.signal)
    .then(data=>{if(!controller.signal.aborted){setStops(data.stops);setStopWarning(data.warning??'');setStopLoading(false)}})
    .catch(e=>{if(!controller.signal.aborted){setError(String(e));setStopLoading(false)}});
   return ()=>controller.abort();
 },[date,mode,activeOnly,sourceIds,catalogLoaded]);
 useEffect(()=>{
   const controller=new AbortController();setSearchStops([]);
   if(!stopSearch.trim()){setSearching(false);return}
   setSearching(true);
   const timer=window.setTimeout(()=>getJSON<{stops:Stop[];warning:string|null}>(endpoints.stops(date,mode,activeOnly,sourceIds,stopSearch.trim()),controller.signal)
    .then(data=>{if(!controller.signal.aborted){setSearchStops(data.stops);setSearching(false)}})
    .catch(e=>{if(!controller.signal.aborted){setError(String(e));setSearching(false)}}),200);
   return ()=>{controller.abort();window.clearTimeout(timer)};
 },[date,mode,activeOnly,sourceIds,stopSearch]);
 useEffect(()=>{
   if(!catalogLoaded)return;
   const controller=new AbortController();setNetworkLoading(true);
   getJSON<NetworkGeometry>(endpoints.network(date,mode,source,activeOnly,sourceIds),controller.signal)
     .then(data=>{if(controller.signal.aborted)return;setNetwork(data);setNetworkLoading(false)})
     .catch(e=>{if(!controller.signal.aborted){setNetworkLoading(false);setError(`Network geometry: ${String(e)}`)}});
   return ()=>controller.abort();
 },[date,mode,source,activeOnly,sourceIds,catalogLoaded]);
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
   const controller=new AbortController();setTimetable(null);
   getJSON<RouteTimetable>(endpoints.timetable(selectedRoute,date),controller.signal)
     .then(data=>{if(!controller.signal.aborted)setTimetable(data)})
     .catch(e=>{if(!controller.signal.aborted)setError(String(e))});
   return ()=>controller.abort();
 },[selectedRoute,date]);
 useEffect(()=>{
   if(selectedRoute&&((source&&selectedRoute.source_id!==source)||hiddenSources.includes(selectedRoute.source_id)||!modeMatches(selectedRoute.route_type,mode)||(activeOnly&&!selectedRoute.active_on_date)))setSelectedRoute(null);
   setSelectedStop(null);setFocused(null);
 },[source,mode,activeOnly,hiddenSources]);
 const shown=useMemo(()=>routes.filter(r=>(!source||r.source_id===source)&&
   modeMatches(r.route_type,mode)&&!hiddenSources.includes(r.source_id)&&
   (!activeOnly||r.active_on_date)&&(`${r.short_name||''} ${r.long_name||''} ${r.source_id}`).toLowerCase().includes(search.toLowerCase())).slice(0,900),
   [routes,source,mode,activeOnly,search,hiddenSources]);
 const counts=summary?.snapshots.filter(s=>(!source||s.source_id===source)&&!hiddenSources.includes(s.source_id)).reduce((a,s)=>({
   routes:a.routes+s.active_route_records,stops:a.stops+s.total_stop_records,trips:a.trips+s.scheduled_trip_instances,
   uncertain:a.uncertain+s.non_exact_frequency_windows}),{routes:0,stops:0,trips:0,uncertain:0});
 const matchingStops=searchStops;
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
     </select>
     {snapshots.map(s=><label className="check" key={s.source_id}><input type="checkbox" aria-label={`Show ${s.source_id}`} checked={!hiddenSources.includes(s.source_id)} onChange={()=>setHiddenSources(ids=>ids.includes(s.source_id)?ids.filter(id=>id!==s.source_id):[...ids,s.source_id])}/>{s.source_id}</label>)}</div>
    <div className="category"><span className="field-label">TRANSPORT MODE</span><div className="mode-tabs">
     {([['all','All'],['bus','Bus'],['rail','Rail'],['tram','Tram'],['ferry','Ferry']] as const).map(([v,label])=><button type="button" key={v} className={mode===v?'active':''} onClick={()=>setMode(v)}>{label}</button>)}</div></div>
    <label className="check"><input type="checkbox" checked={activeOnly} onChange={e=>setActiveOnly(e.target.checked)}/> Operating on selected date only</label>
    <div className="section-heading"><span>ROUTES</span><span>{shown.length}{shown.length===900?'+':''}</span></div>
    <div className="route-list">{shown.length?shown.map(r=><button type="button" className={'route-item '+(selectedRoute&&key(selectedRoute)===key(r)?'selected':'')}
      key={key(r)} onClick={()=>{setSelectedRoute(r);setSelectedStop(null)}}>
      <span className="route-square" style={{background:routeColor(r.route_type)}}/><span className="route-label"><strong>{r.short_name||r.route_id}</strong>
      <small title={r.long_name}>{r.long_name||'Untitled route'}</small><em>{r.source_id}</em></span>
      <span className={'status-dot '+(r.active_on_date?'on':'off')} title={r.active_on_date?'Active':'Inactive on date'}/></button>):<p className="empty">No matching routes. Import GTFS or adjust the filters.</p>}</div>
    <div className="stop-search"><label className="search"><Search size={16}/><input aria-label="Search stops" placeholder="Find stop name or ID…" value={stopSearch} onChange={e=>setStopSearch(e.target.value)}/></label>
      {stopSearch&&<div className="stop-results">{matchingStops.slice(0,40).map(s=><button className="stop-result" type="button" key={s.snapshot_id+s.stop_id} onClick={()=>{setSelectedStop(s);setSelectedRoute(null);setFocused(null);map.current?.easeTo({center:[s.lon,s.lat],zoom:13})}}>{s.name} <small>{s.source_id}</small></button>)}{matchingStops.length>40&&<p>Showing 40 of {matchingStops.length}; refine search.</p>}{!matchingStops.length&&<p>No matching imported stops.</p>}</div>}</div>
    <footer className="sidebar-footer"><Database size={14}/> {snapshots.length} selected source snapshots</footer>
   </aside>
   <main className="main-panel"><div className="metrics"><div><span>OPERATING ROUTES</span><strong>{counts?.routes??'—'}</strong></div>
       <div><span>STOP RECORDS</span><strong>{counts?.stops??'—'}</strong></div>
       <div><span>SCHEDULED TRIPS</span><strong>{counts?.trips??'—'}</strong></div>
       <div><span>NON-EXACT WINDOWS</span><strong>{counts?.uncertain??'—'}</strong></div></div>
     {summary?.snapshots.some(s=>(!source||source===s.source_id)&&!s.service_date_covered)&&<div className="warning"><AlertTriangle size={16}/> One or more imported feeds do not cover this date. Missing service is not confirmed absence.</div>}
     {listWarning&&<div className="warning">{listWarning}</div>}
     {stopWarning&&<div className="warning">{stopWarning}</div>}
     {network.metadata.warning&&<div className="warning"><AlertTriangle size={16}/> {network.metadata.warning}</div>}
     {!!network.metadata.simplification_tolerance_degrees&&<div className="geometry-note">Regional overview simplified within {network.metadata.simplification_tolerance_degrees.toFixed(4)}°. Select an exact departure for its original source alignment.</div>}
     {basemapWarning&&<div className="warning"><WifiOff size={16}/> {basemapWarning}</div>}
     {error&&<div className="warning"><AlertTriangle size={16}/> {error}</div>}
     <div className="map-container"><div ref={mapEl} className="map"/>
       <div className="map-overlay"><strong>Geographic route atlas</strong><span>{networkLoading?'Loading network geometry…':`${new Set(visibleNetwork.features.map(f=>f.properties.uid)).size} drawn routes · ${visibleNetwork.features.length} alignments`}</span></div>
       <div className="map-actions"><button type="button" onClick={()=>{if(map.current)fitNetwork(map.current,focused??visibleNetwork,visibleStops)}} title="Fit routes and stops"><Maximize2 size={15}/> Fit</button>
        <button type="button" onClick={()=>{setBasemap(s=>s==='streets'?'plain':'streets');setBasemapWarning('')}} title="Toggle online street map">
         <MapIcon size={15}/>{basemap==='streets'?'Street map':'Plain map'}</button></div>
       <div className="legend"><span><i style={{background:'#3583be'}}/>Bus</span><span><i style={{background:'#b36b9e'}}/>Rail</span>
        <span><i style={{background:'#da7933'}}/>Tram</span><span><i style={{background:'#229a90'}}/>Ferry</span><span>Schematic links dashed</span></div>
     </div>
     <div className="bottom"><div className="bottom-title"><Clock3 size={16}/> Service coverage by source</div>
       <div className="source-strip">{summary?.snapshots.filter(s=>(!source||s.source_id===source)&&!hiddenSources.includes(s.source_id)).map(s=><div className="source-row" key={s.snapshot_id}>
         <strong>{s.source_id}</strong><span>{s.active_route_records} active routes</span><span>{s.scheduled_trip_instances} scheduled trips</span>
         <small>{s.feed_start}–{s.feed_end}</small></div>)}{!summary?.snapshots.length&&<span className="muted">No imported feeds. See README for an import command.</span>}</div></div>
   </main>
   <aside className="inspector"><Inspector route={selectedRoute} stop={selectedStop} date={date} timetable={timetable} board={board} snapshots={snapshots} onGeometry={setFocused} onDirection={setSelectedDirection} onStop={s=>{setSelectedStop(s);setSelectedRoute(null);setFocused(null)}}/></aside>
  </div><div className="app-status">{loading?'Loading dataset…':networkLoading?'Loading geometry…':stopLoading?'Loading stops…':`Showing ${visibleNetwork.features.length} route alignments`} · {searching?'Searching stops…':''} ·
    {basemap==='streets'?' Online basemap requires internet':' Offline plain map'} · Imported data may be incomplete or expired.</div>
 </div>
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
