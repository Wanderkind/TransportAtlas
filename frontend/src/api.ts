export type Snapshot = {snapshot_id:string; source_id:string; feed_start:string; feed_end:string; retrieved_at:string; content_sha256:string;feed_info?:Record<string,string>};
export type Summary = {date:string; snapshots:Array<{source_id:string; snapshot_id:string; service_date_covered:boolean; total_route_records:number; total_stop_records:number; active_trip_templates:number; active_route_records:number; scheduled_trip_instances:number; non_exact_frequency_windows:number; feed_start:string; feed_end:string}>};
export type Route = {snapshot_id:string; source_id:string; route_id:string; short_name:string; long_name:string; route_type:number; active_on_date:boolean};
export type Stop = {snapshot_id:string; source_id:string; stop_id:string; name:string; lat:number; lon:number};
export type Departure = {trip_id:string; route_short_name:string; service_date:string; gtfs_time:string; local_departure:string; headsign:string; boarding_allowed:boolean; precision:string};
export type FreqWindow = {trip_id:string; start_local:string; end_local:string; headway_seconds:number};
export type ServiceStatus = {status:'scheduled'|'not_scheduled_on_date'|'no_trip_references'|'unknown';label:string;reason:string|null;trip_templates:number;active_trip_templates:number;exact_calls_on_date:number;frequency_templates_on_date:number;service_date:string;basis:string};
export type NearbyStops = {radius_m:number;origin:{snapshot_id:string;stop_id:string;name:string;service?:ServiceStatus};stops:Array<Stop&{distance_m:number;same_source:boolean;service?:ServiceStatus}>;total:number;truncated:boolean;note:string;service_date?:string};
export type QualityCategory = 'publisher'|'coverage'|'timing'|'geometry'|'validation';
export type IntervalFinding = {classification:'implausible'|'inconsistent';reason:string;from_sequence:number;to_sequence:number;from_stop_id:string;to_stop_id:string;from_stop_name?:string|null;to_stop_name?:string|null;from_time:string|null;to_time:string|null;elapsed_seconds:number;straight_line_m?:number;timing_resolution_seconds?:number;min_implied_speed_kph?:number;threshold_kph?:number;trip_id?:string};
export type Indicator = {category:QualityCategory;category_label:string;code:string;level:'info'|'caution';title:string;detail:string;evidence:{examples?:IntervalFinding[];[key:string]:unknown};source_id:string|null;snapshot_id:string|null};
export type TripTiming = {segments_checked:number;segments_untimed:number;segments_without_coordinates:number;threshold_kph:number;distance_basis:string;frequency_based:boolean;findings:IntervalFinding[]};
export type Freshness = {retrieved_at:string;retrieval_note:string;feed_info_present:boolean;publisher_name:string|null;feed_version:string|null;version_label_date:string|null;publication_date:null;publication_note:string;declared_start:string|null;declared_end:string|null;observed_start:string|null;observed_end:string|null;service_date:string;in_observed_coverage:boolean;in_declared_validity:boolean|null;trip_templates_on_date:number|null;busiest_same_weekday:{date:string;trip_templates:number}|null};
export type SourceQuality = {snapshot_id:string;source_id:string;freshness:Freshness;indicators:Indicator[]};
export type RouteQuality = {snapshot_id:string;route_id:string;indicators:Indicator[]};
export type StopQuality = {snapshot_id:string;stop_id:string;service:ServiceStatus;indicators:Indicator[]};
export type StopRoutes = {routes:Array<{snapshot_id:string;route_id:string;short_name:string|null;long_name:string|null;route_type:number;mode:string;trip_templates:number;calls_on_date:number}>;note:string};
export type StopBoard = {stop:Stop; wall_date:string; departures:Departure[]; frequency_windows:FreqWindow[]; warning:string};
export type NetworkFeature = {type:'Feature'; properties:{snapshot_id:string;source_id:string;route_id:string;uid:string;route_type:number;mode:string;color:string;quality:string};geometry:{type:'LineString';coordinates:number[][]}};
export type Coverage = {total_route_records:number;routes_with_trip_references:number;active_routes:number;eligible_routes:number;rendered_routes:number;routes_without_trip_references:number;routes_with_insufficient_geometry:number;routes_omitted_by_limits:number;inactive_routes_excluded:number;partially_rendered_routes:number};
export type NetworkGeometry = {type:'FeatureCollection'; features:NetworkFeature[]; metadata:{service_date:string;active_only:boolean;source_count:number;route_count:number;feature_count:number;point_count:number;truncated:boolean;missing_geometry_routes:number;warning:string|null;simplification_tolerance_degrees?:number;coverage?:Coverage;snapshots?:Array<{source_id:string;coverage:Coverage}>}};
export type TimetableTrip = {trip_id:string;variant_id:string;departure_seconds:number;template_first_seconds:number;direction_id:number|null;headsign:string;first_stop_id:string;first_stop_name:string;last_stop_name:string;departure_local:string;arrival_local:string|null;precision:string};
export type TimetableWindow = {trip_id:string;variant_id:string;direction_id:number|null;headsign:string;first_stop_name:string;start_local:string;end_local:string;headway_seconds:number};
export type Variant = {variant_id:string;direction_id:number|null;shape_id:string|null;representative_trip_id:string;headsign:string;stops:Array<{stop_id:string;name:string}>;scheduled_departures:number;first_departure:string|null;last_departure:string|null;median_interval_minutes:number|null;frequency_windows:number};
export type Provenance = Snapshot & {publisher:string;landing_url:string;download_url:string|null;license_name:string|null};
export type RouteTimetable = {total_departures:number;matching_departures?:number;offset?:number;next_offset?:number|null;departures:TimetableTrip[];truncated:boolean;frequency_windows:TimetableWindow[];windows_truncated:boolean;note:string;first_departure:string|null;last_departure:string|null;variants:Variant[];provenance:Provenance;calendars:Array<Record<string,string|number>>;exceptions:Array<{service_id:string;service_date:string;exception_type:number}>;warnings:Array<{issue_code:string;count:number}>};
export type TripDetail = {trip:{trip_id:string;shape_id:string|null};stops:Array<{stop_id:string;stop_sequence:number;name:string;arrival_sec:number|null;departure_sec:number|null;pickup_type:number;drop_off_type:number;timepoint:number|null;lat:number;lon:number}>;geometry:NetworkGeometry;timing?:TripTiming};

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
 nearby:(s:Stop,d:string,radius=200)=>`/api/v1/stop/${q(s.snapshot_id)}/${q(s.stop_id)}/nearby?radius=${radius}&date=${q(d)}`,
 sourceQuality:(snapshot:string,d:string)=>`/api/v1/quality/source/${q(snapshot)}?date=${q(d)}`,
 routeQuality:(r:Route)=>`/api/v1/quality/route/${q(r.snapshot_id)}/${q(r.route_id)}`,
 stopQuality:(s:Stop,d:string)=>`/api/v1/quality/stop/${q(s.snapshot_id)}/${q(s.stop_id)}?date=${q(d)}`,
 stopRoutes:(s:Stop,d:string)=>`/api/v1/stop/${q(s.snapshot_id)}/${q(s.stop_id)}/routes?date=${q(d)}`,
 board:(s:Stop,d:string)=>`/api/v1/stop/${q(s.snapshot_id)}/${q(s.stop_id)}/departures?date=${q(d)}`,
 timetable:(r:Route,d:string)=>`/api/v1/route/${q(r.snapshot_id)}/${q(r.route_id)}/timetable?date=${q(d)}&limit=200`,
 timetablePage:(r:Route,d:string,direction:string,variant:string,offset:number)=>`/api/v1/route/${q(r.snapshot_id)}/${q(r.route_id)}/timetable?date=${q(d)}&limit=200&offset=${offset}&direction=${q(direction)}&variant=${q(variant)}`,
 trip:(snapshot:string,trip:string)=>`/api/v1/trip/${q(snapshot)}/${q(trip)}`
};

// Phase 6: read-only historical snapshot inventory and comparison.
export type HistorySource = {source_id:string;publisher:string;snapshots:number;ready_snapshots:number;selected_snapshot_id:string|null;comparison_possible:boolean};
export type SnapshotCounts = {route_records:number;routes_with_trip_references:number;stop_records:number;stops_referenced_by_trips:number;trip_templates:number;shape_ids:number};
// Phase 7C: per-snapshot acquisition provenance (never conflated with service or publication dates).
export type ProvenanceKind = 'publisher_direct'|'third_party_archived'|'synthetic'|'unknown_legacy';
export type SnapshotProvenance = {kind:ProvenanceKind;label:string;synthetic:boolean;
 publisher:{name:string|null;landing_url:string|null;reported_origin_url:string|null};download_origin:string|null;
 archive:{provider:string|null;provider_name:string|null;record_url:string|null;version_id:string|null;observed_at:string|null;observed_on?:string|null;observed_at_evidence_url:string|null;download_url:string|null;feed_id?:string|null;acquisition_method?:'provider_download'|'authenticated_browser';delivery_known?:boolean}|null;
 license:{name?:string;attribution?:string;evidence_url?:string;restrictions?:string|null}|null;
 local_retrieved_at?:string;observed_at:string|null;observation_basis:'archive_record'|'archive_record_date'|'local_retrieval'|'unknown';problem?:string|null};
export type SnapshotItem = {snapshot_id:string;source_id:string;content_sha256:string;import_status:string;selected:boolean;comparable:boolean;retrieved_at:string;retrieval_note:string;
 provenance:SnapshotProvenance;observed_at:string|null;observation_basis:SnapshotProvenance['observation_basis'];
 archive:{path:string;present:boolean;bytes:number|null;sha256_verified:boolean|null};
 feed_info:{present:boolean|null;publisher_name:string|null;feed_version:string|null;version_label_date:string|null;declared_start:string|null;declared_end:string|null};
 observed_coverage:{start:string|null;end:string|null};validation:{validator_version:string;validation_date:string;errors:number;warnings:number;infos:number}|null;validation_note:string;
 date_in_coverage?:boolean;counts?:SnapshotCounts};
export type InventorySort = 'retrieved'|'observed';
export type SnapshotInventory = {source_id:string;service_date:string|null;snapshots:SnapshotItem[];ready_snapshots:number;comparison_possible:boolean;note:string;identity_note:string;sort:InventorySort;sort_note:string};
export type MetricDelta = {baseline:number|null;comparison:number|null;delta:number|null;percent_change:number|null;status:'unchanged'|'changed'|'incomparable';percent_note:string;attributable_to_feed_version?:boolean;metric?:string};
export type FieldChange = {field:string;baseline:unknown;comparison:unknown;distance_m?:number};
export type HistoryRecord = {id:string;status:'added'|'removed'|'modified'|'unchanged';changes:FieldChange[];baseline:Record<string,unknown>|null;comparison:Record<string,unknown>|null;service?:MetricDelta|null;moved_m?:number|null;served_on_date?:{baseline:boolean|null;comparison:boolean|null}};
export type RecordPage = {counts:Record<string,number>;total:number;status_filter:string;matching:number;offset:number;limit:number;records:HistoryRecord[];truncated:boolean;next_offset:number|null};
export type ComparisonSide = {snapshot_id:string;content_sha256:string;retrieved_at:string;observed_at:string|null;provenance:SnapshotProvenance;observed_coverage:{start:string;end:string};service_date:string;date_in_coverage:boolean;reason:string|null};
export type SnapshotComparison = {source_id:string;policy:'same_date'|'different_dates';baseline:ComparisonSide;comparison:ComparisonSide;synthetic:boolean;
 notices:Array<{code:string;level:string;text:string}>;identity_note:string;definitions:Record<string,string>;summary:Record<string,MetricDelta>;
 routes:RecordPage;stops:RecordPage;shapes:{counts:Record<string,number>;note:string}};
export type ComparisonRequest = {source:string;baseline:string;comparison:string;policy:'same_date'|'different_dates';date:string;baselineDate:string;comparisonDate:string};
export const historyEndpoints={
 sources:'/api/v1/history/sources',
 snapshots:(source:string,d:string,sort:InventorySort='retrieved')=>`/api/v1/history/${q(source)}/snapshots?date=${q(d)}&sort=${sort}`,
 compare:(r:ComparisonRequest,status='changed',limit=100,offset=0)=>`/api/v1/history/${q(r.source)}/compare?baseline=${q(r.baseline)}&comparison=${q(r.comparison)}&policy=${r.policy}`+
   (r.policy==='same_date'?`&date=${q(r.date)}`:`&baseline_date=${q(r.baselineDate)}&comparison_date=${q(r.comparisonDate)}`)+`&record_status=${q(status)}&record_limit=${limit}&record_offset=${offset}`,
};
