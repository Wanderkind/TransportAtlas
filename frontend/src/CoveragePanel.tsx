import type {Coverage} from './api';

export function CoveragePanel({coverage,snapshots,loading,error,activeOnly}:{coverage?:Coverage;snapshots?:Array<{source_id:string;coverage:Coverage}>;loading:boolean;error:string|null;activeOnly:boolean}){
 if(error)return <div className="coverage-panel" role="status">Coverage unavailable for the current filters.</div>;
 if(loading||!coverage)return <div className="coverage-panel" role="status">Loading coverage for the current filters…</div>;
 const c=coverage;
 return <div className="coverage-panel" aria-label="Network coverage">
  <div><b>{c.total_route_records}</b> route records · <b>{c.routes_with_trip_references}</b> with trips · <b>{c.active_routes}</b> active on date · <b>{c.eligible_routes}</b> eligible · <b>{c.rendered_routes}</b> drawn</div>
  <small>{c.routes_without_trip_references} without trips · {c.routes_with_insufficient_geometry} eligible with insufficient geometry · {c.routes_omitted_by_limits} omitted by limits · {c.partially_rendered_routes} partially drawn · {c.inactive_routes_excluded} inactive excluded</small>
  <details><summary>Coverage definitions and operators</summary>
   <p>Records and trip references use the selected source/mode scope, before date filtering. Eligible routes are {activeOnly?'active routes with trip references':'all routes with trip references'}. Eligible = drawn + insufficient geometry + omitted by limits. Partial routes are a subset of drawn routes. Inactivity is not missing data.</p>
   <table><thead><tr><th>Source</th><th>Records</th><th>Trips</th><th>Active</th><th>Eligible</th><th>Drawn</th><th>No trips</th><th>Insufficient</th><th>Limits</th></tr></thead><tbody>{snapshots?.map(s=><tr key={s.source_id}><td>{s.source_id}</td>{(['total_route_records','routes_with_trip_references','active_routes','eligible_routes','rendered_routes','routes_without_trip_references','routes_with_insufficient_geometry','routes_omitted_by_limits'] as const).map(k=><td key={k}>{s.coverage[k]}</td>)}</tr>)}</tbody></table>
  </details>
 </div>;
}
