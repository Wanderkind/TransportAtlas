import type {FeatureCollection} from 'geojson';
import type {GeoJSONSource, Map as MlMap} from 'maplibre-gl';

type Context = {filters:Record<string,unknown>;network:Record<string,unknown>;stops:Record<string,unknown>;sourceBySnapshot:Record<string,string>;timings?:unknown};

/** Opt-in, read-only local diagnostics. No controls or data writes are exposed. */
export function installDiagnostics(map:MlMap, context:()=>Context){
 if(new URLSearchParams(location.search).get('diagnostics')!=='1')return ()=>{};
 const samples:Record<string,{eventAt:number;loaded:boolean}>={};
 const onData=(event:{sourceId?:string})=>{
   if(event.sourceId&&['network','stops','focused'].includes(event.sourceId)){
     samples[event.sourceId]={eventAt:performance.now(),loaded:map.isSourceLoaded(event.sourceId)};
   }
 };
 map.on('sourcedata',onData);
 const describe=async(id:string,layers:string[])=>{
   const source=map.getSource(id) as GeoJSONSource|undefined;
   const data=source?await source.getData() as FeatureCollection:undefined;
   const features=data?.features??[];
   const rendered=layers.some(layer=>map.getLayer(layer))?map.queryRenderedFeatures({layers:layers.filter(layer=>!!map.getLayer(layer))}):[];
   const sourceOf=(p:Record<string,unknown>|null)=>String(p?.source_id??context().sourceBySnapshot[String(p?.snapshot_id)]??'');
   return {dataRequestKey:(data as FeatureCollection&{request_key?:string}|undefined)?.request_key??null,featureCount:features.length,count:features.length,
     routeCount:new Set(features.map(f=>f.properties?.uid).filter(Boolean)).size,
     sourceIds:Array.from(new Set(features.map(f=>sourceOf(f.properties)))).filter(Boolean).sort(),
     routeUids:Array.from(new Set(features.map(f=>f.properties?.uid).filter(Boolean))),
     sourceLoaded:!!source&&map.isSourceLoaded(id),
     visible:layers.some(layer=>!!map.getLayer(layer)&&map.getLayoutProperty(layer,'visibility')!=='none'),
     renderedFeatureCount:rendered.length,
     renderedSourceIds:Array.from(new Set(rendered.map(f=>sourceOf(f.properties)))).filter(Boolean).sort(),
     quality:features[0]?.properties?.quality??null,
     pointCount:features.reduce((n,f)=>n+(f.geometry.type==='LineString'?f.geometry.coordinates.length:0),0)};
 };
 const diagnostic={inspect:async()=>{
   const state=context();
   return {filters:state.filters,
     network:{...state.network,...await describe('network',['route-network','route-schematic'])},
     stops:{...state.stops,...await describe('stops',['stop-circles','stop-clusters'])},
     focused:await describe('focused',['focused-line','focused-schematic']),
     timings:{requests:state.timings,sources:samples}};
 }};
 const host=window as Window&{transportAtlasDiagnostics?:typeof diagnostic};
 host.transportAtlasDiagnostics=diagnostic;
 return ()=>{map.off('sourcedata',onData);if(host.transportAtlasDiagnostics===diagnostic)delete host.transportAtlasDiagnostics};
}
