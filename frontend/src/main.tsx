import React, {useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState} from 'react';
import {createRoot} from 'react-dom/client';
import * as maplibregl from 'maplibre-gl';
import {Map as MlMap} from 'maplibre-gl';
import workerUrl from 'maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url';
import type {FeatureCollection} from 'geojson';
import 'maplibre-gl/dist/maplibre-gl.css';
import {MapPinned, TrainFront, CalendarDays, Database, Search,
        Layers3, AlertTriangle, Clock3, Map as MapIcon, Maximize2, WifiOff} from './icons';
import {endpoints, NetworkGeometry, Route,
        RouteTimetable, Snapshot, Stop, StopBoard, Summary} from './api';
import './styles.css';
import {Inspector} from './Inspector';
import {installDiagnostics} from './diagnostics';
import {useKeyedRequest} from './useKeyedRequest';
import {CoveragePanel} from './CoveragePanel';
import {SourceGate} from './sourceGate';
maplibregl.setWorkerUrl(workerUrl);

const STREETS_STYLE='https://tiles.openfreemap.org/styles/liberty';
const PLAIN_STYLE:maplibregl.StyleSpecification={version:8,sources:{},layers:[{id:'background',type:'background',paint:{'background-color':'#eef3f0'}}]};
function todayISO(){const requested=new URLSearchParams(window.location.search).get('date');if(requested && /^\d{4}-\d{2}-\d{2}$/.test(requested))return requested;const d=new Date();return `${d.getFullYear()}-${`${d.getMonth()+1}`.padStart(2,'0')}-${`${d.getDate()}`.padStart(2,'0')}`}
function routeColor(n:number){return n===0?'#da7933':[1,2,5,12].includes(n)?'#b36b9e':n===4?'#229a90':[3,11].includes(n)?'#3583be':'#bd863b'}
const emptyGeoJSON=():FeatureCollection=>({type:'FeatureCollection',features:[]});
const emptyNetwork=():NetworkGeometry=>({type:'FeatureCollection',features:[],metadata:{service_date:'',active_only:true,source_count:0,route_count:0,feature_count:0,point_count:0,truncated:false,missing_geometry_routes:0,warning:null}});
const key=(r:{snapshot_id:string;route_id:string})=>`${r.snapshot_id}:${r.route_id}`;
const modeMatches=(n:number,m:string)=>m==='all'||(m==='tram'?n===0:m==='rail'?[1,2,5,12].includes(n):m==='bus'?[3,11].includes(n):n===4);
const stopData=new WeakMap<Stop[],FeatureCollection>();

function ensureLayers(m:MlMap){
 if(!m.getSource('network'))m.addSource('network',{type:'geojson',data:emptyGeoJSON()});
 if(!m.getLayer('route-network'))m.addLayer({layout:{visibility:'none'},id:'route-network',type:'line',source:'network',
    filter:['==',['get','quality'],'source-shape'],
    paint:{'line-color':['get','color'],'line-width':['interpolate',['linear'],['zoom'],5,1.3,9,3],
           'line-opacity':0.66}});
 if(!m.getLayer('route-schematic'))m.addLayer({layout:{visibility:'none'},id:'route-schematic',type:'line',source:'network',
    filter:['==',['get','quality'],'schematic-stop-link'],
    paint:{'line-color':['get','color'],'line-width':2.5,'line-opacity':0.65,'line-dasharray':[3,2]}});
 if(!m.getLayer('route-highlight'))m.addLayer({layout:{visibility:'none'},id:'route-highlight',type:'line',source:'network',
    filter:['==',['get','uid'],'__none__'],
    paint:{'line-color':'#ffb53b','line-width':6,'line-opacity':0.96}});
 if(!m.getLayer('route-highlight-schematic'))m.addLayer({layout:{visibility:'none'},id:'route-highlight-schematic',type:'line',source:'network',filter:['==',['get','uid'],'__none__'],paint:{'line-color':'#ffb53b','line-width':6,'line-opacity':0.96,'line-dasharray':[3,2]}});
 if(!m.getSource('focused'))m.addSource('focused',{type:'geojson',data:emptyGeoJSON()});
 if(!m.getLayer('focused-line'))m.addLayer({layout:{visibility:'none'},id:'focused-line',type:'line',source:'focused',filter:['==',['get','quality'],'source-shape'],paint:{'line-color':'#ffb53b','line-width':7}});
 if(!m.getLayer('focused-schematic'))m.addLayer({layout:{visibility:'none'},id:'focused-schematic',type:'line',source:'focused',filter:['==',['get','quality'],'schematic-stop-link'],paint:{'line-color':'#ffb53b','line-width':7,'line-dasharray':[3,2]}});
 if(!m.getSource('stops'))m.addSource('stops',{type:'geojson',data:emptyGeoJSON(),cluster:true,clusterRadius:35,clusterMaxZoom:10});
 if(!m.getLayer('stop-clusters'))m.addLayer({layout:{visibility:'none'},id:'stop-clusters',type:'circle',source:'stops',filter:['has','point_count'],paint:{'circle-radius':13,'circle-color':'#1d6489','circle-opacity':0.7}});
 if(!m.getLayer('stop-circles'))m.addLayer({layout:{visibility:'none'},id:'stop-circles',type:'circle',source:'stops',filter:['!', ['has','point_count']],
    paint:{'circle-radius':['interpolate',['linear'],['zoom'],5,2,10,4],
           'circle-color':'#f9fcff','circle-stroke-width':1.4,'circle-stroke-color':'#1d6489'}});
}
function showNetwork(m:MlMap,gate:SourceGate,net:NetworkGeometry,stops:Stop[],selected:Route|null,focused:NetworkGeometry|null,direction:string,networkReady:boolean,stopsReady:boolean,requestKey:string){
 if(!m.getSource('network'))return;
 // Identity changes hide that source's layers synchronously below and send the
 // new data; layers reappear only once SourceGate confirms the current identity.
 gate.set('network',{type:'FeatureCollection',features:net.features,request_key:requestKey} as FeatureCollection,net);
 gate.set('focused',(focused??emptyGeoJSON()) as FeatureCollection,focused);
 let points=stopData.get(stops);
 if(!points){points={type:'FeatureCollection',features:stops.filter(s=>Number.isFinite(s.lat)&&Number.isFinite(s.lon)).map(s=>({type:'Feature' as const,properties:{snapshot_id:s.snapshot_id,source_id:s.source_id,stop_id:s.stop_id},geometry:{type:'Point' as const,coordinates:[s.lon,s.lat]}})),request_key:requestKey} as FeatureCollection;stopData.set(stops,points)}
 gate.set('stops',points,stops);
 for(const [source,layers,valid] of [
   ['network',['route-network','route-schematic','route-highlight','route-highlight-schematic'],networkReady],
   ['stops',['stop-clusters','stop-circles'],stopsReady],
   ['focused',['focused-line','focused-schematic'],networkReady&&!!focused]
 ] as const){
   const visibility=valid&&gate.ready(source)?'visible':'none';
   for(const layer of layers)if(m.getLayer(layer)&&m.getLayoutProperty(layer,'visibility')!==visibility)m.setLayoutProperty(layer,'visibility',visibility);
 }
 for(const [layer,quality] of [['route-highlight','source-shape'],['route-highlight-schematic','schematic-stop-link']]){
   if(m.getLayer(layer))m.setFilter(layer, ['all',['==',['get','uid'],selected&&!focused?key(selected):'__none__'],['==',['get','quality'],quality],direction==='all'?true:['in',direction,['get','direction_ids']]]);
 }
 if(m.getLayer('route-network'))m.setPaintProperty('route-network','line-opacity',selected?0.24:0.66);
 if(m.getLayer('route-schematic'))m.setPaintProperty('route-schematic','line-opacity',selected?0.24:0.65);
}
/** The current network is drawn once its gate opened and its visible tiles finished loading. */
const networkDrawn=(m:MlMap,gate:SourceGate)=>gate.ready('network')&&m.getLayoutProperty('route-network','visibility')==='visible'&&m.isSourceLoaded('network');
function fitNetwork(m:MlMap,net:NetworkGeometry,stops:Stop[]){
 const bounds=new maplibregl.LngLatBounds();
 for(const f of net.features)for(const p of f.geometry.coordinates){if(Number.isFinite(p[0])&&Number.isFinite(p[1]))bounds.extend([p[0],p[1]])}
 if(bounds.isEmpty())for(const stop of stops){if(Number.isFinite(stop.lat)&&Number.isFinite(stop.lon))bounds.extend([stop.lon,stop.lat])}
 if(!bounds.isEmpty())m.fitBounds(bounds,{padding:55,maxZoom:11,duration:500});
}

const EMPTY_NETWORK=emptyNetwork(),EMPTY_STOPS:Stop[]=[],EMPTY_ROUTES:Route[]=[],EMPTY_SNAPSHOTS:Snapshot[]=[];
function App(){
 const [date,setDate]=useState(todayISO());
 const [mode,setMode]=useState('all'),[source,setSource]=useState(''),[search,setSearch]=useState(''),[activeOnly,setActiveOnly]=useState(true);
 const [hiddenSources,setHiddenSources]=useState<string[]>([]),[stopSearch,setStopSearch]=useState('');
 const snapshotRequest=useKeyedRequest<Snapshot[]>(endpoints.snapshots,'snapshots');
 const snapshots=snapshotRequest.data??EMPTY_SNAPSHOTS;
 const catalogLoaded=snapshotRequest.status==='ready';
 const sourceIds=snapshots.filter(s=>(!source||s.source_id===source)&&!hiddenSources.includes(s.source_id)).map(s=>s.source_id).sort().join(',');
 const selectionKey=snapshots.map(s=>s.snapshot_id).sort().join(',');
 const filterKey=JSON.stringify([date,mode,source,sourceIds,activeOnly,selectionKey]);
 const dateKey=JSON.stringify([date,selectionKey]);
 const networkRequest=useKeyedRequest<NetworkGeometry>(catalogLoaded?endpoints.network(date,mode,source,activeOnly,sourceIds):null,filterKey);
 const stopRequest=useKeyedRequest<{stops:Stop[];warning:string|null}>(catalogLoaded?endpoints.stops(date,mode,activeOnly,sourceIds):null,filterKey);
 const routeRequest=useKeyedRequest<{routes:Route[];warning:string|null}>(catalogLoaded?endpoints.routes(date):null,dateKey);
 const summaryRequest=useKeyedRequest<Summary>(catalogLoaded?endpoints.summary(date):null,dateKey);
 const searchRequest=useKeyedRequest<{stops:Stop[];warning:string|null;total:number}>(catalogLoaded&&stopSearch.trim()?endpoints.stops(date,mode,activeOnly,sourceIds,stopSearch.trim()):null,JSON.stringify([filterKey,stopSearch.trim()]),200);
 const network=networkRequest.data??EMPTY_NETWORK,stops=stopRequest.data?.stops??EMPTY_STOPS,routes=routeRequest.data?.routes??EMPTY_ROUTES,summary=summaryRequest.data;
 const networkLoading=!catalogLoaded||networkRequest.status==='loading',stopLoading=catalogLoaded&&stopRequest.status==='loading';
 const loading=snapshotRequest.status==='loading'||summaryRequest.status==='loading'||routeRequest.status==='loading';
 const searching=searchRequest.status==='loading';
 const listWarning=routeRequest.data?.warning,stopWarning=stopRequest.data?.warning;
 const error=[snapshotRequest.error&&`Catalog: ${snapshotRequest.error}`,networkRequest.error&&`Network unavailable; previous geometry hidden. ${networkRequest.error}`,stopRequest.error&&`Stop markers unavailable; previous markers hidden. ${stopRequest.error}`,routeRequest.error,summaryRequest.error,searchRequest.error].filter(Boolean).join(' ');
 const [selection,setSelection]=useState<{context:string;route:Route|null;stop:Stop|null}>({context:'',route:null,stop:null});
 const selectedRoute=selection.context===filterKey&&selection.route?routes.find(r=>key(r)===key(selection.route!)&&modeMatches(r.route_type,mode)&&sourceIds.split(',').includes(r.source_id)&&(!activeOnly||r.active_on_date))??null:null;
 const selectedStop=selection.context===filterKey?selection.stop:null;
 const timetableRequest=useKeyedRequest<RouteTimetable>(selectedRoute?endpoints.timetable(selectedRoute,date):null,`${filterKey}|route|${selectedRoute?key(selectedRoute):''}`);
 const boardRequest=useKeyedRequest<StopBoard>(selectedStop?endpoints.board(selectedStop,date):null,`${filterKey}|stop|${selectedStop?.snapshot_id}:${selectedStop?.stop_id}`);
 const timetable=timetableRequest.data,board=boardRequest.data;
 const [focus,setFocus]=useState<{context:string;route:string;geometry:NetworkGeometry|null}|null>(null);
 const routeUid=selectedRoute?key(selectedRoute):'';
 const focused=focus?.context===filterKey&&focus.route===routeUid?focus.geometry:null;
 const setFocused=useCallback((geometry:NetworkGeometry|null)=>setFocus(previous=>previous?.context===filterKey&&previous.route===routeUid&&previous.geometry===geometry?previous:{context:filterKey,route:routeUid,geometry}),[filterKey,routeUid]);
 const [selectedDirection,setSelectedDirection]=useState('all');
 const [basemap,setBasemap]=useState<'streets'|'plain'>(new URLSearchParams(location.search).get('basemap')==='plain'?'plain':'streets'),[basemapWarning,setBasemapWarning]=useState('');
 const [renderedKey,setRenderedKey]=useState('');
 const mapEl=useRef<HTMLDivElement>(null),map=useRef<MlMap|null>(null),gateRef=useRef<SourceGate|null>(null),hasFit=useRef(false),previousStyle=useRef(basemap);
 const scopeRef=useRef(filterKey);scopeRef.current=filterKey;
 const chooseRoute=useCallback((route:Route)=>setSelection({context:scopeRef.current,route,stop:null}),[]);
 const chooseStop=useCallback((stop:Stop)=>setSelection({context:scopeRef.current,route:null,stop}),[]);
 const visibleNetwork=network,visibleStops=stops;
 const display=useRef({network:visibleNetwork,stops:visibleStops,route:selectedRoute,focused,direction:selectedDirection,networkReady:false,stopsReady:false,key:filterKey});
 display.current={network:visibleNetwork,stops:visibleStops,route:selectedRoute,focused,direction:selectedDirection,networkReady:networkRequest.status==='ready',stopsReady:stopRequest.status==='ready',key:filterKey};
 const routesRef=useRef(routes);routesRef.current=routes;
 const diagnosticState=useRef({filters:{},network:{},stops:{},sourceBySnapshot:{},timings:{}});
 diagnosticState.current={filters:{key:filterKey,date,mode,source,sources:sourceIds?sourceIds.split(','):[],activeOnly},
  network:{requestKey:networkRequest.status==='ready'?filterKey:null,status:networkRequest.status,error:networkRequest.error},
  stops:{requestKey:stopRequest.status==='ready'?filterKey:null,status:stopRequest.status,error:stopRequest.error},
  sourceBySnapshot:Object.fromEntries(snapshots.map(s=>[s.snapshot_id,s.source_id])),
  timings:{network:{startedAt:networkRequest.startedAt,receivedAt:networkRequest.receivedAt},stops:{startedAt:stopRequest.startedAt,receivedAt:stopRequest.receivedAt}}};
 useEffect(()=>{
   if(!mapEl.current||map.current)return;
   const m=new maplibregl.Map({container:mapEl.current,style:basemap==='plain'?PLAIN_STYLE:STREETS_STYLE,center:[9.13,40],zoom:7});map.current=m;
   const removeDiagnostics=installDiagnostics(m,()=>diagnosticState.current);
   m.addControl(new maplibregl.NavigationControl(),'top-right');
   // Every map mutation below runs outside MapLibre's render pass: either from
   // style.load or from a coalesced animation frame, never inside a map event
   // fired by Style.update().
   let frame:number|null=null;
   const schedule=()=>{if(frame===null)frame=requestAnimationFrame(()=>{frame=null;update()})};
   const gate=new SourceGate(m,schedule);gateRef.current=gate;
   const update=()=>{
     if(!m.getSource('network'))gate.reset();
     ensureLayers(m);const d=display.current;
     showNetwork(m,gate,d.network,d.stops,d.route,d.focused,d.direction,d.networkReady,d.stopsReady,d.key);
     if(d.networkReady&&networkDrawn(m,gate))setRenderedKey(d.key);
     if(!hasFit.current&&d.networkReady&&(d.network.features.length||d.stops.length)){fitNetwork(m,d.network,d.stops);hasFit.current=true}
   };
   m.on('style.load',update);
   // A diffed basemap swap can drop our sources without style.load; re-add them.
   m.on('styledata',()=>{if(!m.getSource('network'))schedule()});
   // Tile completion only updates the "drawn" status; visibility never depends on it.
   m.on('sourcedata',event=>{if(['network','stops','focused'].includes(event.sourceId??''))schedule()});
   m.on('error',()=>{if(previousStyle.current==='streets'){setBasemapWarning('Online basemap unavailable; switched to the plain offline map.');setBasemap('plain')}});
   const openRoute=(evt:maplibregl.MapLayerMouseEvent)=>{
     const properties=evt.features?.[0]?.properties;if(!properties)return;
     const route=routesRef.current.find(r=>r.snapshot_id===properties.snapshot_id&&r.route_id===properties.route_id);
     if(route)chooseRoute(route);
   };
   for(const layer of ['route-network','route-schematic','route-highlight','route-highlight-schematic'])m.on('click',layer,openRoute);
   m.on('click','stop-clusters',evt=>{const f=evt.features?.[0];if(f?.geometry.type==='Point')m.easeTo({center:f.geometry.coordinates as [number,number],zoom:Math.min(m.getZoom()+2,12)})});
   m.on('click','stop-circles',evt=>{const p=evt.features?.[0]?.properties;const stop=display.current.stops.find(s=>s.snapshot_id===p?.snapshot_id&&s.stop_id===p?.stop_id);if(stop)chooseStop(stop)});
   for(const layer of ['route-network','route-schematic','stop-circles']){m.on('mouseenter',layer,()=>{m.getCanvas().style.cursor='pointer'});m.on('mouseleave',layer,()=>{m.getCanvas().style.cursor=''})}
   return ()=>{if(frame!==null)cancelAnimationFrame(frame);gate.destroy();gateRef.current=null;removeDiagnostics();map.current=null;m.remove()};
 },[]);
 useEffect(()=>{if(map.current&&previousStyle.current!==basemap){previousStyle.current=basemap;map.current.setStyle(basemap==='streets'?STREETS_STYLE:PLAIN_STYLE)}},[basemap]);
 useLayoutEffect(()=>{
   const m=map.current,gate=gateRef.current;if(!m?.getSource('network')||!gate)return;
   showNetwork(m,gate,visibleNetwork,visibleStops,selectedRoute,focused,selectedDirection,networkRequest.status==='ready',stopRequest.status==='ready',filterKey);
   if(networkRequest.status==='ready'&&networkDrawn(m,gate))setRenderedKey(filterKey);
 },[visibleNetwork,visibleStops,selectedRoute,focused,selectedDirection,filterKey,networkRequest.status,stopRequest.status]);
 const shown=useMemo(()=>routes.filter(r=>(!source||r.source_id===source)&&modeMatches(r.route_type,mode)&&!hiddenSources.includes(r.source_id)&&(!activeOnly||r.active_on_date)&&(`${r.route_id} ${r.short_name||''} ${r.long_name||''} ${r.source_id}`).toLowerCase().includes(search.toLowerCase())).slice(0,900),[routes,source,mode,activeOnly,search,hiddenSources]);
 const counts=summary?.snapshots.filter(s=>(!source||s.source_id===source)&&!hiddenSources.includes(s.source_id)).reduce((a,s)=>({
   routes:a.routes+s.active_route_records,stops:a.stops+s.total_stop_records,trips:a.trips+s.scheduled_trip_instances,
   uncertain:a.uncertain+s.non_exact_frequency_windows}),{routes:0,stops:0,trips:0,uncertain:0});
 const matchingStops=searchRequest.data?.stops??EMPTY_STOPS;
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
    <label className="check"><input type="checkbox" aria-label="Operating on selected date only" checked={activeOnly} onChange={e=>setActiveOnly(e.target.checked)}/> Operating on selected date only</label>
    <div className="section-heading"><span>ROUTES</span><span>{shown.length}{shown.length===900?'+':''}</span></div>
    <div className="route-list">{routeRequest.status==='loading'?<p className="empty" role="status">Loading route catalog…</p>:shown.length?shown.map(r=><button type="button" className={'route-item '+(selectedRoute&&key(selectedRoute)===key(r)?'selected':'')}
      data-route-uid={key(r)} key={key(r)} onClick={()=>{chooseRoute(r)}}>
      <span className="route-square" style={{background:routeColor(r.route_type)}}/><span className="route-label"><strong>{r.short_name||r.route_id}</strong>
      <small title={r.long_name}>{r.long_name||'Untitled route'}</small><em>{r.source_id}</em></span>
      <span className={'status-dot '+(r.active_on_date?'on':'off')} title={r.active_on_date?'Active':'Inactive on date'}/></button>):<p className="empty">No matching routes. Import GTFS or adjust the filters.</p>}</div>
    <div className="stop-search"><label className="search"><Search size={16}/><input aria-label="Search stops" placeholder="Find stop name or ID…" value={stopSearch} onChange={e=>setStopSearch(e.target.value)}/></label>
      {stopSearch&&<div className="stop-results">{matchingStops.slice(0,40).map(s=><button className="stop-result" type="button" key={s.snapshot_id+s.stop_id} onClick={()=>{chooseStop(s);map.current?.easeTo({center:[s.lon,s.lat],zoom:13})}}>{s.name} <small>{s.source_id}</small></button>)}{matchingStops.length>40&&<p>Showing 40 of {matchingStops.length}; refine search.</p>}{searching?<p role="status">Searching current stops…</p>:!matchingStops.length&&<p>No matching imported stops.</p>}{searchRequest.data?.warning&&<p role="alert">{searchRequest.data.warning}</p>}</div>}</div>
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
     <CoveragePanel coverage={network.metadata.coverage} snapshots={network.metadata.snapshots} loading={networkLoading} error={networkRequest.error} activeOnly={activeOnly}/>
     {!!network.metadata.simplification_tolerance_degrees&&<div className="geometry-note">Regional overview simplified within {network.metadata.simplification_tolerance_degrees.toFixed(4)}°. Select an exact departure for its original source alignment.</div>}
     {basemapWarning&&<div className="warning"><WifiOff size={16}/> {basemapWarning}</div>}
     {error&&<div className="warning" role="alert"><AlertTriangle size={16}/> {error}<button type="button" onClick={()=>{snapshotRequest.retry();networkRequest.retry();stopRequest.retry();routeRequest.retry();summaryRequest.retry()}}>Retry current data</button></div>}
     <div className="map-container"><div ref={mapEl} className="map"/>
       <div className="map-overlay"><strong>Geographic route atlas</strong><span>{networkRequest.status==='error'?'Network unavailable; previous data hidden.':networkLoading?'Loading current network…':renderedKey!==filterKey?'Drawing current network…':`${new Set(visibleNetwork.features.map(f=>f.properties.uid)).size} drawn routes · ${visibleNetwork.features.length} alignments`}</span></div>
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
   <aside className="inspector" data-filter-key={filterKey}><Inspector key={`${filterKey}|${routeUid}|${selectedStop?.stop_id??''}`} route={selectedRoute} stop={selectedStop} date={date} timetable={timetable} board={board} timetableError={timetableRequest.error} boardError={boardRequest.error} snapshots={snapshots} onGeometry={setFocused} onDirection={setSelectedDirection} onStop={chooseStop}/></aside>
  </div><div className="app-status">{loading?'Loading dataset…':networkLoading?'Loading geometry…':stopLoading?'Loading current stops…':renderedKey!==filterKey&&networkRequest.status==='ready'?'Drawing current network…':`Showing ${visibleNetwork.features.length} route alignments`} · {searching?'Searching stops…':''} ·
    {basemap==='streets'?' Online basemap requires internet':' Offline plain map'} · Imported data may be incomplete or expired.</div>
 </div>
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
