export type Snapshot = {snapshot_id:string; source_id:string; feed_start:string; feed_end:string; retrieved_at:string; content_sha256:string};
export type Summary = {date:string; snapshots:Array<{source_id:string; snapshot_id:string; service_date_covered:boolean; total_route_records:number; total_stop_records:number; active_trip_templates:number; active_route_records:number; scheduled_trip_instances:number; non_exact_frequency_windows:number; feed_start:string; feed_end:string}>};
export type Route = {snapshot_id:string; source_id:string; route_id:string; short_name:string; long_name:string; route_type:number; active_on_date:boolean};
export type Stop = {snapshot_id:string; stop_id:string; name:string; lat:number; lon:number};
export type Departure = {trip_id:string; route_short_name:string; service_date:string; gtfs_time:string; local_departure:string; headsign:string; boarding_allowed:boolean; precision:string};
export type FreqWindow = {trip_id:string; start_local:string; end_local:string; headway_seconds:number};
export type StopBoard = {stop:Stop; wall_date:string; departures:Departure[]; frequency_windows:FreqWindow[]; warning:string};
export type NetworkFeature = {type:'Feature'; properties:{snapshot_id:string;source_id:string;route_id:string;uid:string;route_type:number;mode:string;color:string;quality:string};geometry:{type:'LineString';coordinates:number[][]}};
export type NetworkGeometry = {type:'FeatureCollection'; features:NetworkFeature[]; metadata:{service_date:string;active_only:boolean;source_count:number;route_count:number;feature_count:number;point_count:number;truncated:boolean;missing_geometry_routes:number;warning:string|null}};
export type TimetableTrip = {trip_id:string;direction_id:number|null;headsign:string;first_stop_id:string;first_stop_name:string;last_stop_name:string;departure_local:string;arrival_local:string|null;precision:string};
export type TimetableWindow = {trip_id:string;direction_id:number|null;headsign:string;first_stop_name:string;start_local:string;end_local:string;headway_seconds:number};
export type RouteTimetable = {total_departures:number;departures:TimetableTrip[];truncated:boolean;frequency_windows:TimetableWindow[];windows_truncated:boolean;note:string};

export async function getJSON<T>(url:string, signal?:AbortSignal):Promise<T>{
 const result=await fetch(url,{signal});
 if(!result.ok){let detail=result.statusText;try{detail=(await result.json()).detail||detail}catch{}throw new Error(`${result.status}: ${detail}`)}
 return result.json() as Promise<T>;
}
const q=(s:string)=>encodeURIComponent(s);
export const endpoints={
 snapshots:'/api/v1/snapshots',
 summary:(d:string)=>`/api/v1/summary?date=${q(d)}`,
 routes:(d:string)=>`/api/v1/routes?date=${q(d)}`,
 stops:'/api/v1/stops?limit=20000',
 network:(d:string,m:string,s:string,active:boolean)=>`/api/v1/network/geometry?date=${q(d)}&mode=${q(m)}&active_only=${active}${s?`&source=${q(s)}`:''}`,
 board:(s:Stop,d:string)=>`/api/v1/stop/${q(s.snapshot_id)}/${q(s.stop_id)}/departures?date=${q(d)}`,
 timetable:(r:Route,d:string)=>`/api/v1/route/${q(r.snapshot_id)}/${q(r.route_id)}/timetable?date=${q(d)}&limit=200`,
 trip:(snapshot:string,trip:string)=>`/api/v1/trip/${q(snapshot)}/${q(trip)}`
};
