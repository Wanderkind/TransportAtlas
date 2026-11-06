import type {FeatureCollection} from 'geojson';
import type {GeoJSONSource, Map as MlMap} from 'maplibre-gl';

type Entry = {identity:unknown; applied:unknown; failed:unknown; hiddenAtRender:number; shownAtRender:number|null};

/**
 * Decides when a GeoJSON source may be shown for its current data identity.
 *
 * MapLibre 6 only uploads tile buffers for sources whose layers are visible when
 * `Style.update()` runs, and a source whose layers are all hidden reports
 * `isSourceLoaded() === true` without loading tiles. Gating visibility on
 * `isSourceLoaded` therefore oscillates (show -> tiles load -> "not loaded" ->
 * hide -> "loaded" -> show), and toggling visibility from a `sourcedata` event
 * fired inside `Style.update()` lets the painter draw buckets that were never
 * uploaded (`VertexArrayObject.bind` reading `undefined.length`).
 *
 * Instead a source becomes eligible once (a) the `setData` promise for its
 * current identity has resolved in the worker without a source error and
 * (b) at least one full map frame has rendered since it was hidden, so that
 * frame already dropped any tiles built from the previous data. Callers hide
 * layers synchronously when the identity changes and apply visibility only
 * outside MapLibre's render pass.
 *
 * MapLibre's `setData` promise resolves even when the worker rejects the data;
 * the failure arrives as a map `error` event carrying `sourceId`. Such an
 * identity stays hidden and is reported through `failed()`.
 */
export class SourceGate {
 private renders=0;
 private entries:Record<string,Entry>={};
 constructor(private map:MlMap, private onChange:()=>void){
   map.on('render',this.onRender);
   map.on('error',this.onError);
 }
 private onRender=()=>{
   this.renders++;
   if(Object.values(this.entries).some(e=>(e.applied===e.identity&&this.renders===e.hiddenAtRender+1)||
     (e.shownAtRender!==null&&this.renders===e.shownAtRender+1)))this.onChange();
 };
 private onError=(event:unknown)=>{
   const id=(event as {sourceId?:string}).sourceId;
   const entry=id?this.entries[id]:undefined;
   if(entry&&entry.applied!==entry.identity){entry.failed=entry.identity;this.onChange()}
 };
 /** Sends new data when the identity changes. Returns true if the identity changed. */
 set(id:string,data:FeatureCollection,identity:unknown):boolean{
   const source=this.map.getSource(id) as GeoJSONSource|undefined;if(!source)return false;
   const entry=this.entries[id]??={identity:undefined,applied:undefined,failed:undefined,hiddenAtRender:-1,shownAtRender:null};
   if(entry.identity===identity)return false;
   entry.identity=identity;entry.applied=undefined;entry.failed=undefined;entry.hiddenAtRender=this.renders;entry.shownAtRender=null;
   source.setData(data).then(()=>{
     if(entry.identity===identity&&entry.failed!==identity){entry.applied=identity;this.map.triggerRepaint();this.onChange()}
   },()=>{entry.failed=identity;this.onChange()});
   return true;
 }
 /** True when the worker holds the current identity and a frame has rendered since hiding. */
 ready(id:string):boolean{
   const entry=this.entries[id];
   return !!entry&&entry.identity!==undefined&&entry.applied===entry.identity&&this.renders>entry.hiddenAtRender;
 }
 /** True when MapLibre rejected the current identity's data. */
 failed(id:string):boolean{
   const entry=this.entries[id];
   return !!entry&&entry.identity!==undefined&&entry.failed===entry.identity;
 }
 /** Record that the caller just made this source's layers visible (or hid them). */
 shown(id:string,visible:boolean){
   const entry=this.entries[id];if(!entry)return;
   if(!visible)entry.shownAtRender=null;
   else if(entry.shownAtRender===null)entry.shownAtRender=this.renders;
 }
 /**
  * True once visible layers have been through a full render (so MapLibre marks
  * the source as used and requests its tiles) and those tiles finished loading.
  */
 drawn(id:string):boolean{
   const entry=this.entries[id];
   return this.ready(id)&&!!entry&&entry.shownAtRender!==null&&this.renders>entry.shownAtRender&&this.map.isSourceLoaded(id);
 }
 /** Forget all source state, e.g. after a style swap recreated the sources. */
 reset(){this.entries={}}
 destroy(){this.map.off('render',this.onRender);this.map.off('error',this.onError)}
}
