export type Snapshot = {snapshot_id:string; source_id:string; feed_start:string; feed_end:string; retrieved_at:string; content_sha256:string;feed_info?:Record<string,string>};
export type Summary = {date:string; snapshots:Array<{source_id:string; snapshot_id:string; service_date_covered:boolean; total_route_records:number; total_stop_records:number; active_trip_templates:number; active_route_records:number; scheduled_trip_instances:number; non_exact_frequency_windows:number; feed_start:string; feed_end:string}>};
export type Route = {snapshot_id:string; source_id:string; route_id:string; short_name:string; long_name:string; route_type:number; active_on_date:boolean};
export type Stop = {snapshot_id:string; source_id:string; stop_id:string; name:string; lat:number; lon:number};
export type Departure = {trip_id:string; route_short_name:string; service_date:string; gtfs_time:string; local_departure:string; headsign:string; boarding_allowed:boolean; precision:string};
export type FreqWindow = {trip_id:string; start_local:string; end_local:string; headway_seconds:number};
export type NearbyStops = {radius_m:number;stops:Array<Stop&{distance_m:number;same_source:boolean}>;total:number;truncated:boolean;note:string};
export type StopBoard = {stop:Stop; wall_date:string; departures:Departure[]; frequency_windows:FreqWindow[]; warning:string};
export type NetworkFeature = {type:'Feature'; properties:{snapshot_id:string;source_id:string;route_id:string;uid:string;route_type:number;mode:string;color:string;quality:string};geometry:{type:'LineString';coordinates:number[][]}};
export type Coverage = {total_route_records:number;routes_with_trip_references:number;active_routes:number;eligible_routes:number;rendered_routes:number;routes_without_trip_references:number;routes_with_insufficient_geometry:number;routes_omitted_by_limits:number;inactive_routes_excluded:number;partially_rendered_routes:number};
export type NetworkGeometry = {type:'FeatureCollection'; features:NetworkFeature[]; metadata:{service_date:string;active_only:boolean;source_count:number;route_count:number;feature_count:number;point_count:number;truncated:boolean;missing_geometry_routes:number;warning:string|null;simplification_tolerance_degrees?:number;coverage?:Coverage;snapshots?:Array<{source_id:string;coverage:Coverage}>}};
export type TimetableTrip = {trip_id:string;variant_id:string;departure_seconds:number;template_first_seconds:number;direction_id:number|null;headsign:string;first_stop_id:string;first_stop_name:string;last_stop_name:string;departure_local:string;arrival_local:string|null;precision:string};
export type TimetableWindow = {trip_id:string;variant_id:string;direction_id:number|null;headsign:string;first_stop_name:string;start_local:string;end_local:string;headway_seconds:number};
export type Variant = {variant_id:string;direction_id:number|null;shape_id:string|null;representative_trip_id:string;headsign:string;stops:Array<{stop_id:string;name:string}>;scheduled_departures:number;first_departure:string|null;last_departure:string|null;median_interval_minutes:number|null;frequency_windows:number};
export type Provenance = Snapshot & {publisher:string;landing_url:string;download_url:string|null;license_name:string|null};
export type RouteTimetable = {total_departures:number;matching_departures?:number;offset?:number;next_offset?:number|null;departures:TimetableTrip[];truncated:boolean;frequency_windows:TimetableWindow[];windows_truncated:boolean;note:string;first_departure:string|null;last_departure:string|null;variants:Variant[];provenance:Provenance;calendars:Array<Record<string,string|number>>;exceptions:Array<{service_id:string;service_date:string;exception_type:number}>;warnings:Array<{issue_code:string;count:number}>};
export type TripDetail = {trip:{trip_id:string;shape_id:string|null};stops:Array<{stop_id:string;stop_sequence:number;name:string;arrival_sec:number|null;departure_sec:number|null;pickup_type:number;drop_off_type:number;timepoint:number|null;lat:number;lon:number}>;geometry:NetworkGeometry};

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
 stops:(d:string,m:string,active:boolean,sources?:string,search?:string)=>`/api/v1/stops?limit=20000&date=${q(d)}&mode=${q(m)}&active_only=${active}${sources!==undefined?`&sources=${q(sources)}`:''}${search?`&search=${q(search)}`:''}`,
 network:(d:string,m:string,s:string,active:boolean,sources?:string)=>`/api/v1/network/geometry?date=${q(d)}&mode=${q(m)}&active_only=${active}${s?`&source=${q(s)}`:''}${sources!==undefined?`&sources=${q(sources)}`:''}`,
 nearby:(s:Stop,radius=200)=>`/api/v1/stop/${q(s.snapshot_id)}/${q(s.stop_id)}/nearby?radius=${radius}`,
 board:(s:Stop,d:string)=>`/api/v1/stop/${q(s.snapshot_id)}/${q(s.stop_id)}/departures?date=${q(d)}`,
 timetable:(r:Route,d:string)=>`/api/v1/route/${q(r.snapshot_id)}/${q(r.route_id)}/timetable?date=${q(d)}&limit=200`,
 timetablePage:(r:Route,d:string,direction:string,variant:string,offset:number)=>`/api/v1/route/${q(r.snapshot_id)}/${q(r.route_id)}/timetable?date=${q(d)}&limit=200&offset=${offset}&direction=${q(direction)}&variant=${q(variant)}`,
 trip:(snapshot:string,trip:string)=>`/api/v1/trip/${q(snapshot)}/${q(trip)}`
};
