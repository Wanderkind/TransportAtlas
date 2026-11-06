import type {FeatureCollection} from 'geojson';
import type {GeoJSONSource, Map as MlMap} from 'maplibre-gl';

type Entry = {identity:unknown; applied:unknown; hiddenAtRender:number};

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
 * current identity has resolved in the worker and (b) at least one full map
 * frame has rendered since it was hidden, so that frame already dropped any
 * tiles built from the previous data. Callers hide layers synchronously when
 * the identity changes and apply visibility only outside MapLibre's render pass.
 */
export class SourceGate {
 private renders=0;
 private entries:Record<string,Entry>={};
 constructor(private map:MlMap, private onChange:()=>void){
   map.on('render',this.onRender);
 }
 private onRender=()=>{
   this.renders++;
   if(Object.values(this.entries).some(e=>e.applied===e.identity&&this.renders===e.hiddenAtRender+1))this.onChange();
 };
 /** Sends new data when the identity changes. Returns true if the identity changed. */
 set(id:string,data:FeatureCollection,identity:unknown):boolean{
   const source=this.map.getSource(id) as GeoJSONSource|undefined;if(!source)return false;
   const entry=this.entries[id]??={identity:undefined,applied:undefined,hiddenAtRender:-1};
   if(entry.identity===identity)return false;
   entry.identity=identity;entry.applied=undefined;entry.hiddenAtRender=this.renders;
   source.setData(data).then(()=>{if(entry.identity===identity){entry.applied=identity;this.map.triggerRepaint();this.onChange()}},
     ()=>{/* superseded or failed update: stay hidden until a later identity applies */});
   return true;
 }
 /** True when the worker holds the current identity and a frame has rendered since hiding. */
 ready(id:string):boolean{
   const entry=this.entries[id];
   return !!entry&&entry.identity!==undefined&&entry.applied===entry.identity&&this.renders>entry.hiddenAtRender;
 }
 /** Forget all source state, e.g. after a style swap recreated the sources. */
 reset(){this.entries={}}
 destroy(){this.map.off('render',this.onRender)}
}
