import {useEffect, useMemo, useState} from 'react';
import type {ComparisonRequest, HistoryRecord, HistorySource, InventorySort, MetricDelta, ProvenanceKind, RecordPage, SnapshotComparison, SnapshotInventory, SnapshotItem, SnapshotProvenance} from './api';
import {historyEndpoints} from './api';
import {useKeyedRequest} from './useKeyedRequest';

const short=(id:string)=>id.slice(0,8);
const fmt=(v:unknown)=>v===null||v===undefined?'—':Array.isArray(v)?v.map(x=>String(x)).join(', ')||'—':typeof v==='number'?String(Math.round(v*1e6)/1e6):String(v);
const SUMMARY:Array<[string,string,boolean]>=[
 ['route_records','Route records',false],['routes_with_trip_references','Routes with trip references',false],
 ['routes_scheduled','Routes scheduled on the service date',true],
 ['stop_records','Stop records',false],['stops_referenced_by_trips','Stop records referenced by trips',false],
 ['stops_served','Stop records served on the service date',true],
 ['trip_templates','Trip templates',false],['trip_templates_on_date','Trip templates operating on the date',true],
 ['exact_trip_instances','Exact scheduled trip instances',true],['frequency_only_templates','Frequency-only templates (no exact times)',true],
 ['non_exact_windows','Non-exact headway windows',true],['shape_ids','Shape IDs',false]];

function Metric({m}:{m:MetricDelta}){
 if(m.status==='incomparable')return <><td>{fmt(m.baseline)}</td><td>{fmt(m.comparison)}</td><td colSpan={2} className="incomparable">Not comparable: a side is outside its calendar coverage</td></>;
 return <><td>{fmt(m.baseline)}</td><td>{fmt(m.comparison)}</td><td>{m.delta!>0?'+':''}{m.delta}</td><td title={m.percent_note}>{m.percent_change===null?(m.baseline===0&&m.delta?'undefined (zero baseline)':'—'):`${m.percent_change>0?'+':''}${m.percent_change}%`}</td></>;
}

// Phase 7C: how each copy was obtained. An archived copy is attributed to its archive, never to a download of ours.
const KIND_SHORT:Record<ProvenanceKind,string>={publisher_direct:'Publisher download',third_party_archived:'Archived copy',synthetic:'Synthetic fixture',unknown_legacy:'Unknown provenance'};
const BASIS:Record<SnapshotProvenance['observation_basis'],string>={archive_record:'per the archive record',archive_record_date:'date only, per the archive record',local_retrieval:'our own download',unknown:'not recorded'};
const day=(v:string|null|undefined)=>v?v.slice(0,10):'unknown';

function ProvenanceCell({p}:{p:SnapshotProvenance}){
 return <td data-field="provenance" data-provenance-kind={p.kind} title={p.label}>
  <b>{KIND_SHORT[p.kind]}</b>{p.synthetic&&p.kind!=='synthetic'&&<em className="status-tag">synthetic</em>}
  {p.kind==='third_party_archived'&&p.archive&&<small data-field="archive-provider">from {p.archive.provider_name??p.archive.provider} · archive version {p.archive.version_id?.slice(0,8)??'—'} · originally published by {p.publisher.name??'unknown publisher'}; not downloaded from the publisher by this computer</small>}
  {p.kind==='third_party_archived'&&p.archive?.acquisition_method==='authenticated_browser'&&<small data-field="acquisition">downloaded through the user's browser with their own account; delivery address not recorded</small>}
  {p.kind==='publisher_direct'&&<small>downloaded by this computer from the publisher's URL</small>}
  {p.kind==='unknown_legacy'&&<small>No usable provenance record; shown as unknown, not inferred.</small>}
  {p.license?.name&&<small data-field="license">{p.license.name}{p.license.attribution?` · ${p.license.attribution}`:''}</small>}
  {p.problem&&<small className="caution-text">{p.problem}</small>}
 </td>;
}

function SnapshotTable({items,date}:{items:SnapshotItem[];date:string}){
 return <div className="table-scroll"><table className="history-table snapshots" aria-label="Archived snapshots">
  <thead><tr><th>Snapshot</th><th>How obtained</th><th title="When the feed was observed being published: per the archive record for archived copies, or our own download">Observed published</th><th title="When this computer obtained the copy">Obtained locally</th><th>Publisher version label</th><th>Declared validity</th><th>Calendar coverage</th><th>{date} in coverage</th><th>Counts</th><th>Archive</th><th>Validation</th></tr></thead>
  <tbody>{items.map(s=><tr key={s.snapshot_id} data-snapshot-id={s.snapshot_id} data-selected={s.selected?'true':'false'} className={s.comparable?'':'not-comparable'}>
   <td><code>{short(s.snapshot_id)}</code>{s.selected&&<em className="current-tag">current atlas</em>}{!s.comparable&&<em className="status-tag">{s.import_status}</em>}</td>
   <ProvenanceCell p={s.provenance}/>
   <td data-field="observed">{s.observed_at?day(s.observed_at):'Unknown'}<small>{BASIS[s.observation_basis]}</small></td>
   <td data-field="retrieved">{s.retrieved_at.slice(0,19).replace('T',' ')}</td>
   <td>{s.feed_info.present===false?'Unknown (no feed_info.txt)':s.feed_info.feed_version??'Not supplied'}</td>
   <td>{s.feed_info.declared_start||s.feed_info.declared_end?`${s.feed_info.declared_start??'—'} – ${s.feed_info.declared_end??'—'}`:'Not declared'}</td>
   <td>{s.observed_coverage.start??'—'} – {s.observed_coverage.end??'—'}</td>
   <td data-field="in-coverage">{s.date_in_coverage===undefined?'—':s.date_in_coverage?'Yes':'No (service unknown)'}</td>
   <td>{s.counts?`${s.counts.route_records} routes · ${s.counts.stop_records} stops · ${s.counts.trip_templates} trip templates`:'—'}</td>
   <td>{s.archive.present?'Present':'Missing'}</td>
   <td title={s.validation_note}>{s.validation?`v${s.validation.validator_version}: ${s.validation.errors} E / ${s.validation.warnings} W`:'Unavailable'}</td>
  </tr>)}</tbody></table></div>;
}

/** A missing record is "no record"; "unknown" is only for dates outside calendar coverage. */
const served=(record:unknown,value:boolean|null|undefined)=>!record?'no record':value===null||value===undefined?'unknown (date outside coverage)':value?'yes':'no';

function Changes({r}:{r:HistoryRecord}){
 if(r.status==='added'||r.status==='removed'){
   const v=(r.comparison??r.baseline)!;
   return <span>{fmt(v.short_name??v.name)} {v.long_name?`· ${fmt(v.long_name)}`:''}{v.lat!==undefined?` · ${fmt(v.lat)}, ${fmt(v.lon)}`:''}</span>;
 }
 if(!r.changes.length)return <span className="muted">No field changes</span>;
 return <ul className="field-changes">{r.changes.map(c=><li key={c.field} data-field={c.field}><b>{c.field}</b>: {fmt(c.baseline)} → {fmt(c.comparison)}{c.distance_m!==undefined?` (${c.distance_m} m)`:''}</li>)}</ul>;
}

/** Paged ID-level record differences, fetched for one kind and status filter. */
function RecordTable({kind,request,requestKey}:{kind:'routes'|'stops';request:ComparisonRequest;requestKey:string}){
 const [status,setStatus]=useState('changed');
 const [pages,setPages]=useState<{key:string;items:RecordPage[]}>({key:'',items:[]});
 const tableKey=`${requestKey}|${kind}|${status}`;
 const loaded=pages.key===tableKey?pages.items:[];
 const offset=loaded.length?loaded.at(-1)!.next_offset:0;
 const [wanted,setWanted]=useState(0);
 const target=loaded.length===0?0:wanted===offset&&offset!==null?offset:null;
 const page=useKeyedRequest<SnapshotComparison>(target===null?null:historyEndpoints.compare(request,status,100,target),`${tableKey}|${target}`);
 useEffect(()=>{if(page.status==='ready'&&page.data&&target!==null)setPages(p=>({key:tableKey,items:[...(p.key===tableKey?p.items:[]),page.data![kind]].filter((x,i,all)=>all.findIndex(y=>y.offset===x.offset)===i)}))},[page.status,page.data,target,tableKey,kind]);
 const records=loaded.flatMap(p=>p.records),last=loaded.at(-1);
 return <section className="record-table" data-kind={kind} aria-label={`${kind} record changes`}>
  <div className="record-head"><h3>{kind==='routes'?'Route records':'Stop records'}</h3>
   {last&&<span className="muted">{Object.entries(last.counts).map(([k,v])=>`${v} ${k}`).join(' · ')}</span>}
   <label>Show <select aria-label={`${kind} status filter`} value={status} onChange={e=>{setStatus(e.target.value);setWanted(0)}}>
    {['changed','added','removed','modified','unchanged','all'].map(s=><option key={s} value={s}>{s}</option>)}</select></label></div>
  {page.error&&<p role="alert">Records unavailable: {page.error} <button type="button" onClick={page.retry}>Retry</button></p>}
  {!last&&!page.error&&<p role="status">Loading {kind}…</p>}
  {last&&<div className="table-scroll"><table className="history-table records"><thead><tr><th>ID</th><th>Record status</th><th>Values / changes</th>{kind==='routes'?<th>Trip templates on date</th>:<th>Served on date</th>}</tr></thead>
   <tbody>{records.map(r=><tr key={r.id} data-record-id={r.id} data-record-status={r.status}><td><code>{r.id}</code></td><td><span className={`record-status ${r.status}`}>{r.status}</span></td><td><Changes r={r}/></td>
    {kind==='routes'?<td>{!r.service?'—':r.service.status==='incomparable'?'not comparable':`${r.baseline?fmt(r.service.baseline):'no record'} → ${r.comparison?fmt(r.service.comparison):'no record'}`}</td>
     :<td>{served(r.baseline,r.served_on_date?.baseline)} → {served(r.comparison,r.served_on_date?.comparison)}</td>}</tr>)}</tbody></table></div>}
  {last&&!records.length&&<p className="muted">No {status==='changed'?'changed':status} records.</p>}
  {last&&<p className="muted">Showing {records.length} of {last.matching} matching records{last.truncated?'.':''}</p>}
  {last?.truncated&&<button type="button" className="load-more" disabled={page.status==='loading'} onClick={()=>setWanted(last.next_offset!)}>{page.status==='loading'?'Loading…':'Load more records'}</button>}
 </section>;
}

function Comparison({request}:{request:ComparisonRequest}){
 const key=JSON.stringify(request);
 const result=useKeyedRequest<SnapshotComparison>(historyEndpoints.compare(request,'changed',1,0),`${key}|summary`);
 const c=result.data;
 if(result.error)return <p role="alert" className="history-error">Comparison unavailable: {result.error} <button type="button" onClick={result.retry}>Retry</button></p>;
 if(!c)return <p role="status">Comparing snapshots…</p>;
 const differentDates=c.policy==='different_dates';
 return <section className="comparison" data-policy={c.policy} data-baseline={c.baseline.snapshot_id} data-comparison={c.comparison.snapshot_id}>
  {c.synthetic&&<p className="synthetic-banner" data-synthetic="true">Synthetic test feeds. This comparison demonstrates the method; it is not observed history of any real network.</p>}
  <h3>{differentDates?'Feed version and service date comparison':'Feed version comparison on one service date'}</h3>
  <div className="sides">{([['Baseline',c.baseline],['Comparison',c.comparison]] as const).map(([label,s])=><div key={label} className="side" data-side={label.toLowerCase()} data-in-coverage={String(s.date_in_coverage)}>
   <b>{label}</b> <code>{short(s.snapshot_id)}</code><span>Service date {s.service_date} · calendars {s.observed_coverage.start} – {s.observed_coverage.end}</span>
   <span data-field="side-provenance" data-provenance-kind={s.provenance.kind}>{KIND_SHORT[s.provenance.kind]}{s.provenance.kind==='third_party_archived'&&s.provenance.archive?` from ${s.provenance.archive.provider_name??s.provenance.archive.provider}`:''} · observed {day(s.observed_at)} · obtained locally {s.retrieved_at.slice(0,10)} (neither is a publication or service date)</span>{!s.date_in_coverage&&<span className="caution-text">{s.reason}</span>}</div>)}</div>
  {c.notices.map(n=><p key={n.code} className={`quality-item ${n.level==='info'?'info':'caution'} history-notice`} data-notice={n.code}>{n.text}</p>)}
  <div className="table-scroll"><table className="history-table summary" aria-label="Summary metrics"><thead><tr><th>Metric</th><th>Baseline</th><th>Comparison</th><th>Change</th><th>% of baseline</th></tr></thead>
   <tbody>{SUMMARY.map(([k,label,dated])=>c.summary[k]&&<tr key={k} data-metric={k} data-metric-status={c.summary[k].status}><td title={c.definitions[k]??''}>{label}{dated&&<small> (date-dependent)</small>}</td><Metric m={c.summary[k]}/></tr>)}</tbody></table></div>
  <p className="muted">Shape IDs: {Object.entries(c.shapes.counts).map(([k,v])=>`${v} ${k}`).join(' · ')}. {c.shapes.note}</p>
  <p className="muted identity-note">{c.identity_note}</p>
  <RecordTable kind="routes" request={request} requestKey={key}/>
  <RecordTable kind="stops" request={request} requestKey={key}/>
  <details><summary>Metric definitions</summary>{Object.entries(c.definitions).map(([k,v])=><p key={k}><b>{k}</b>: {v}</p>)}</details>
 </section>;
}

/** Read-only historical snapshot explorer. Never changes the atlas's current selection. */
export function History({date,onClose}:{date:string;onClose:()=>void}){
 const sourcesRequest=useKeyedRequest<{sources:HistorySource[]}>(historyEndpoints.sources,'history-sources');
 const sources=sourcesRequest.data?.sources??[];
 const [source,setSource]=useState('');
 const chosen=source||sources.find(s=>s.comparison_possible)?.source_id||sources[0]?.source_id||'';
 const [inventoryDate,setInventoryDate]=useState(date);
 const [sort,setSort]=useState<InventorySort>('observed');
 const inventory=useKeyedRequest<SnapshotInventory>(chosen?historyEndpoints.snapshots(chosen,inventoryDate,sort):null,`${chosen}|${inventoryDate}|${sort}|inventory`);
 const ready=useMemo(()=>inventory.data?.snapshots.filter(s=>s.comparable)??[],[inventory.data]);
 const [form,setForm]=useState<ComparisonRequest|null>(null);
 const defaults:ComparisonRequest|null=ready.length>=2?{source:chosen,baseline:ready[0].snapshot_id,comparison:ready.at(-1)!.snapshot_id,policy:'same_date',date:inventoryDate,baselineDate:inventoryDate,comparisonDate:inventoryDate}:null;
 const current=form&&form.source===chosen?form:defaults;
 const [submitted,setSubmitted]=useState<ComparisonRequest|null>(null);
 const shown=submitted&&submitted.source===chosen?submitted:null;
 const update=(patch:Partial<ComparisonRequest>)=>current&&setForm({...current,...patch});
 const stale=shown&&current&&JSON.stringify(shown)!==JSON.stringify(current);
 return <div className="history-view" data-view="history">
  <div className="history-header"><div><h2>History · compare archived feed snapshots</h2>
   <p className="muted">Read-only. Browsing or comparing snapshots never changes the snapshots used by the atlas. How each copy was obtained, when it was observed published, when this computer obtained it, the publisher's version label and the GTFS service date are kept separate.</p></div>
   <button type="button" className="back-to-atlas" onClick={onClose}>← Back to atlas</button></div>
  {sourcesRequest.error&&<p role="alert">Sources unavailable: {sourcesRequest.error}</p>}
  <div className="history-controls">
   <label>Operator <select aria-label="History operator" value={chosen} onChange={e=>{setSource(e.target.value);setForm(null);setSubmitted(null)}}>
    {sources.map(s=><option key={s.source_id} value={s.source_id}>{s.source_id} ({s.ready_snapshots} snapshot{s.ready_snapshots===1?'':'s'})</option>)}</select></label>
   <label>Coverage check date <input aria-label="Coverage check date" type="date" value={inventoryDate} onChange={e=>setInventoryDate(e.target.value)}/></label>
   <label>Order snapshots by <select aria-label="Snapshot order" value={sort} onChange={e=>setSort(e.target.value as InventorySort)}>
    <option value="observed">Observation time (when seen published)</option><option value="retrieved">Local retrieval time (when obtained here)</option></select></label>
  </div>
  {inventory.error?<p role="alert">Snapshots unavailable: {inventory.error} <button type="button" onClick={inventory.retry}>Retry</button></p>
   :!inventory.data?<p role="status">Loading archived snapshots…</p>:<>
   <SnapshotTable items={inventory.data.snapshots} date={inventoryDate}/>
   <p className="muted sort-note" data-sort={inventory.data.sort}>{inventory.data.sort_note}</p>
   <p className={inventory.data.comparison_possible?'muted':'quality-item info single-snapshot'} data-comparison-possible={String(inventory.data.comparison_possible)}>{inventory.data.note}</p>
   {current&&<form className="compare-form" onSubmit={e=>{e.preventDefault();setSubmitted(current)}}>
    <label>Baseline <select aria-label="Baseline snapshot" value={current.baseline} onChange={e=>update({baseline:e.target.value})}>{ready.map(s=><option key={s.snapshot_id} value={s.snapshot_id}>{short(s.snapshot_id)} · {s.feed_info.feed_version??'no version label'} · {KIND_SHORT[s.provenance.kind]} · observed {day(s.observed_at)} · obtained {s.retrieved_at.slice(0,10)}</option>)}</select></label>
    <label>Comparison <select aria-label="Comparison snapshot" value={current.comparison} onChange={e=>update({comparison:e.target.value})}>{ready.map(s=><option key={s.snapshot_id} value={s.snapshot_id}>{short(s.snapshot_id)} · {s.feed_info.feed_version??'no version label'} · {KIND_SHORT[s.provenance.kind]} · observed {day(s.observed_at)} · obtained {s.retrieved_at.slice(0,10)}</option>)}</select></label>
    <fieldset><legend>Service-date policy</legend>
     <label><input type="radio" name="policy" aria-label="Same service date" checked={current.policy==='same_date'} onChange={()=>update({policy:'same_date'})}/> Same service date in both (feed version change only)</label>
     <label><input type="radio" name="policy" aria-label="Different service dates" checked={current.policy==='different_dates'} onChange={()=>update({policy:'different_dates'})}/> Two different service dates (version <em>and</em> date change)</label>
    </fieldset>
    {current.policy==='same_date'?<label>Service date <input aria-label="Comparison service date" type="date" value={current.date} onChange={e=>update({date:e.target.value})}/></label>
     :<><label>Baseline date <input aria-label="Baseline service date" type="date" value={current.baselineDate} onChange={e=>update({baselineDate:e.target.value})}/></label>
       <label>Comparison date <input aria-label="Comparison service date (second)" type="date" value={current.comparisonDate} onChange={e=>update({comparisonDate:e.target.value})}/></label></>}
    {current.baseline===current.comparison&&<p className="caution-text">Both sides are the same snapshot; only the dates can differ.</p>}
    <button type="submit" className="run-compare">Compare snapshots</button>
   </form>}
   {stale&&<p className="caution-text" data-stale="true">Inputs changed. The results below are still for the previous inputs; press Compare to update them.</p>}
   {shown&&<Comparison key={JSON.stringify(shown)} request={shown}/>}
  </>}
 </div>;
}
