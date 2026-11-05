export type Snapshot = {snapshot_id:string; source_id:string; feed_start:string; feed_end:string; retrieved_at:string; content_sha256:string};
export type Summary = {date:string; snapshots:Array<{source_id:string; snapshot_id:string; service_date_covered:boolean; total_route_records:number; total_stop_records:number; active_trip_templates:number; active_route_records:number; scheduled_trip_instances:number; non_exact_frequency_windows:number; feed_start:string; feed_end:string}>};
export type Route = {snapshot_id:string; source_id:string; route_id:string; short_name:string; long_name:string; route_type:number; active_on_date:boolean};
export type Stop = {snapshot_id:string; stop_id:string; name:string; lat:number; lon:number};
export type Departure = {trip_id:string; route_short_name:string; service_date:string; gtfs_time:string; local_departure:string; headsign:string; boarding_allowed:boolean; precision:string};
export type FreqWindow = {trip_id:string; start_local:string; end_local:string; headway_seconds:number};
export type StopBoard = {stop:Stop; wall_date:string; departures:Departure[]; frequency_windows:FreqWindow[]; warning:string};

export async function getJSON<T>(url:string):Promise<T>{
 const result=await fetch(url);
 if(!result.ok){let detail=result.statusText;try{detail=(await result.json()).detail||detail}catch{}throw new Error(`${result.status}: ${detail}`)}
 return result.json() as Promise<T>;
}
const q=(s:string)=>encodeURIComponent(s);
export const endpoints={
 snapshots:'/api/v1/snapshots',
 summary:(d:string)=>`/api/v1/summary?date=${q(d)}`,
 routes:(d:string)=>`/api/v1/routes?date=${q(d)}`,
 stops:'/api/v1/stops',
 geometry:(r:Route)=>`/api/v1/route/${q(r.snapshot_id)}/${q(r.route_id)}/geometry`,
 board:(s:Stop,d:string)=>`/api/v1/stop/${q(s.snapshot_id)}/${q(s.stop_id)}/departures?date=${q(d)}`
};
