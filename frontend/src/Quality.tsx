import type {Indicator, IntervalFinding, Route, ServiceStatus, Snapshot, SourceQuality, Stop, StopQuality, RouteQuality} from './api';
import {endpoints} from './api';
import {useKeyedRequest} from './useKeyedRequest';

/** Fixed order so every inspector groups indicators the same way. */
const ORDER = ['publisher','coverage','timing','geometry','validation'] as const;
const LABELS:Record<string,string> = {publisher:'Publisher data limitation',coverage:'Missing or incomplete coverage',timing:'Suspicious scheduled times',geometry:'Schematic or uncertain geometry',validation:'Technical validation'};

export function intervalText(f:IntervalFinding){
 const from=f.from_stop_name||f.from_stop_id,to=f.to_stop_name||f.to_stop_id;
 if(f.classification==='inconsistent')return f.reason==='departure_before_arrival'
   ?`Departs ${f.to_time} before arriving ${f.from_time} at ${from}`
   :`${to} is timed ${f.to_time}, before ${from} at ${f.from_time}`;
 return `${from} ${f.from_time} → ${to} ${f.to_time}: ${f.elapsed_seconds} s for ${Math.round(f.straight_line_m??0)} m straight-line (≥ ${Math.round(f.min_implied_speed_kph??0)} km/h implied; threshold ${f.threshold_kph} km/h)`;
}

/** Restrained, grouped list of source-backed indicators. Never on the map. */
export function QualityNotes({indicators,title='Data quality'}:{indicators:Indicator[];title?:string}){
 if(!indicators.length)return null;
 return <section className="quality" aria-label={title}>
  {ORDER.filter(c=>indicators.some(i=>i.category===c)).map(c=><div className="quality-group" key={c} data-category={c}>
   <span className="quality-category">{LABELS[c]}</span>
   {indicators.filter(i=>i.category===c).map(i=><div key={i.code} className={`quality-item ${i.level}`} data-quality-code={i.code} data-level={i.level}>
     <strong>{i.title}</strong><p>{i.detail}</p>
     {!!i.evidence.examples?.length&&<ul className="quality-examples">{i.evidence.examples.map((f,n)=><li key={n}>{f.trip_id?`Trip ${f.trip_id}: `:''}{intervalText(f)}</li>)}</ul>}
     {i.source_id&&<small>{i.source_id} · snapshot {i.snapshot_id?.slice(0,8)}</small>}
   </div>)}
  </div>)}
 </section>;
}

export function ServiceBadge({service}:{service?:ServiceStatus}){
 if(!service)return null;
 const text=service.status==='scheduled'?`${service.exact_calls_on_date} exact calls${service.frequency_templates_on_date?` + ${service.frequency_templates_on_date} frequency windows`:''}`
   :service.status==='not_scheduled_on_date'?'no trips this date':service.status==='no_trip_references'?'no trips reference it':'status unknown';
 return <em className={`service-status ${service.status}`} data-service-status={service.status} title={service.reason??service.label}>{text}</em>;
}

const range=(a:string|null,b:string|null,missing:string)=>a||b?`${a??'—'} – ${b??'—'}`:missing;

/** Feed provenance, freshness, coverage and validation for one snapshot on the selected date. */
export function SourcePanel({snapshot,date,open=false}:{snapshot:Snapshot|undefined;date:string;open?:boolean}){
 const request=useKeyedRequest<SourceQuality>(snapshot?endpoints.sourceQuality(snapshot.snapshot_id,date):null,`${snapshot?.snapshot_id}|${date}|source-quality`);
 if(!snapshot)return null;
 const q=request.data,f=q?.freshness;
 const cautions=q?.indicators.filter(i=>i.level==='caution'&&i.category==='coverage')??[];
 return <details className="provenance source-quality" open={open||undefined} data-snapshot-id={snapshot.snapshot_id}>
  <summary>Source, freshness and data quality{q?` · ${q.indicators.length} notes`:''}</summary>
  {request.error?<p role="alert">Source quality unavailable: {request.error}</p>:!q||!f?<p role="status">Loading source details…</p>:<>
   <table className="freshness"><tbody>
    <tr><th>Local copy retrieved</th><td data-field="retrieved">{f.retrieved_at}<small>{f.retrieval_note}</small></td></tr>
    <tr><th>Publisher version label</th><td data-field="version">{!f.feed_info_present?'Unknown (no feed_info.txt)':f.feed_version??'Not supplied'}{f.version_label_date&&<small>Label contains the date {f.version_label_date}.</small>}</td></tr>
    <tr><th>Publication date</th><td data-field="publication">Unknown<small>{f.publication_note}</small></td></tr>
    <tr><th>Declared validity</th><td data-field="declared">{range(f.declared_start,f.declared_end,f.feed_info_present?'Not declared':'Unknown (no feed_info.txt)')}</td></tr>
    <tr><th>Calendar coverage</th><td data-field="observed">{range(f.observed_start,f.observed_end,'None')}<small>Dates the imported service calendars cover.</small></td></tr>
    <tr><th>Selected date</th><td data-field="selected">{!f.in_observed_coverage?`${f.service_date}: outside the calendars, service unknown`
      :`${f.service_date}: ${f.trip_templates_on_date} trip templates scheduled`}
      {f.in_observed_coverage&&f.busiest_same_weekday&&<small>Busiest same weekday in this feed: {f.busiest_same_weekday.trip_templates} on {f.busiest_same_weekday.date}.</small>}</td></tr>
    <tr><th>Archive SHA-256</th><td><code>{snapshot.content_sha256}</code></td></tr>
   </tbody></table>
   {cautions.length>0&&!open&&<p className="muted">Includes {cautions.length} coverage caution{cautions.length>1?'s':''}.</p>}
   <QualityNotes indicators={q.indicators} title="Source data quality"/>
  </>}
 </details>;
}

export function RouteQualityNotes({route}:{route:Route}){
 const request=useKeyedRequest<RouteQuality>(endpoints.routeQuality(route),`${route.snapshot_id}|${route.route_id}|route-quality`);
 if(request.error)return <p className="muted" role="status">Route data-quality checks unavailable: {request.error}</p>;
 if(!request.data)return <p className="muted" role="status">Checking route data quality…</p>;
 return request.data.indicators.length?<QualityNotes indicators={request.data.indicators} title="Route data quality"/>
   :<p className="muted quality-clear" data-quality-clear="route">No route-level data-quality notes.</p>;
}

export function StopQualityNotes({stop,date}:{stop:Stop;date:string}){
 const request=useKeyedRequest<StopQuality>(endpoints.stopQuality(stop,date),`${stop.snapshot_id}|${stop.stop_id}|${date}|stop-quality`);
 const q=request.data;
 if(request.error)return <p className="muted" role="status">Stop service status unavailable: {request.error}</p>;
 if(!q)return <p className="muted" role="status">Checking stop service status…</p>;
 return <><p className="stop-service" data-service-status={q.service.status}>{q.service.label}{q.service.status==='no_trip_references'?' in this feed snapshot':` (service day ${q.service.service_date})`}{q.service.status==='scheduled'&&`: ${q.service.exact_calls_on_date} exact calls${q.service.frequency_templates_on_date?`, ${q.service.frequency_templates_on_date} frequency windows without exact times`:''}`}.</p>
  <QualityNotes indicators={q.indicators} title="Stop data quality"/></>;
}
