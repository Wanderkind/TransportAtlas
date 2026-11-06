"""Live-data research job: evidence in artifacts, never in git history.

Run only when explicitly requested in CI or locally; official upstream outages are
reported as data-gathering failures, not passed-off as successful validation.
"""
from __future__ import annotations
import json
import os
from datetime import date
from pathlib import Path
import sys
import time

from transport_atlas.official import catalog, discover, download, _get, _Anchors
from transport_atlas.ingest import import_file
from transport_atlas.storage import connect
from transport_atlas.schedule import daily_summary


def attempt(*, source: str, root: Path, service_day: date) -> dict:
    result = {'source_id': source, 'status': 'not_verified', 'landing_url': catalog()['sources'][source]['landing_url']}
    try:
        t0 = time.monotonic()
        info = discover(source)
        result['candidates'] = info['resource_candidates'][:5]
        result['discovery_seconds'] = round(time.monotonic() - t0, 2)
        if not info['resource_candidates']:
            result['error'] = 'No official GTFS resource URL discoverable from current page HTML'
            try:
                raw, final = _get(result['landing_url'])
                html = raw.decode('utf-8-sig', errors='replace')
                parser = _Anchors(); parser.feed(html)
                relevant = [a for a in parser.items if
                            any(x in (a['text']+' '+a['href']).lower() for x in ('gtfs','orari','download','zip','.csv','.txt'))]
                result['diagnostic_links'] = [{k:str(a.get(k,''))[:350] for k in ('text','href')}
                                               for a in relevant[-20:]]
                needles=['Quadri orari ASPO','Quadri orari ATP Sassari','Risorse']
                for needle in needles:
                    i=html.lower().rfind(needle.lower())
                    if i>=0:
                        result['html_near_resource'] = html[max(0,i-300):i+800].replace('\n',' ')[:1100]
                        break
            except Exception as diag_exc:
                result['diagnostic_error']=str(diag_exc)[:250]
            return result
        output = root / (source + '.zip')
        provenance = download(source, output)
        result['download'] = provenance
        result['download_seconds_total'] = round(time.monotonic() - t0, 2)
        db = connect(root / (source + '.duckdb'), backend='duckdb')
        try:
            imported = import_file(db, output, source_id=source, archive_dir=root / 'archived',
                                  publisher=catalog()['sources'][source]['publisher'],
                                  landing_url=provenance['landing_url'])
            stats = daily_summary(db, service_day)
            result['import'] = imported
            result['summary'] = stats
            result['status'] = 'verified'
        finally:
            db.close()
        result['total_seconds'] = round(time.monotonic() - t0, 2)
    except Exception as exc:
        result['error'] = f'{type(exc).__name__}: {exc}'[:1400]
    return result


def main():
    root = Path(os.environ.get('TRANSPORT_ATLAS_LIVE_DIR','data/live-verification'))
    root.mkdir(parents=True, exist_ok=True)
    date_value = date.fromisoformat(os.environ.get('TRANSPORT_ATLAS_LIVE_DATE','2026-11-06'))
    # Starting with the two smaller operators provides a useful importer smoke
    # test without overcommitting runner resources to a huge regional feed.
    sources = os.environ.get('TRANSPORT_ATLAS_LIVE_SOURCES','aspo,atp_sassari').split(',')
    results=[]
    for src in sources:
        src=src.strip()
        if not src:continue
        result = attempt(source=src, root=root, service_day=date_value)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False, default=str), flush=True)
    summary = {'service_date':date_value.isoformat(), 'sources':results,
               'verified_sources':[x['source_id'] for x in results if x['status']=='verified'],
               'disclaimer':'This evidence covers only downloaded and successfully imported snapshots, not all Sardinian services.'}
    report = root / 'report.json'
    report.write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str),encoding='utf-8')
    md=['# Sardinia live GTFS compatibility probe',
        f"Service date: `{date_value}`. Validated sources: **{len(summary['verified_sources'])}/{len(results)}**.",
        '', '| Source | Status | Note |', '|---|---|---|']
    for x in results:
        if x['status']=='verified':
            z=x['summary']['snapshots'][0] if x['summary']['snapshots'] else {}
            note=f"{x['import']['feeds']['routes']} routes, {x['import']['feeds']['stops']} stops, {z.get('scheduled_trip_instances','?')} scheduled trips; {x.get('total_seconds','?')}s"
        else:
            note=x.get('error','Unverified')
        note=str(note).replace('|','/').replace('\n',' ')[:500]
        md.append(f"| `{x['source_id']}` | {x['status']} | {note} |")
        if x.get('diagnostic_links') or x.get('html_near_resource'):
            md.extend(['',f"<details><summary>{x['source_id']} discovery diagnostics</summary>",'',
                       '```json',json.dumps({'links':x.get('diagnostic_links',[]),'html_fragment':x.get('html_near_resource','')},ensure_ascii=False,indent=2)[:5000],'```','</details>',''])
    md.extend(['','Sources: official Sardegna Mobilità operator pages. Raw ZIP files are excluded from the uploaded artifact and source control.'])
    text='\n'.join(md)+'\n'
    (root / 'report.md').write_text(text,encoding='utf-8')
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as f: f.write(text)
    # A failure is never presented as success. For exploratory CI the job reports
    # reachability limitations separately and remains non-blocking for code review.
    if not summary['verified_sources']:
        print('NO LIVE DATASET VERIFIED. Browser/manual ZIP download may be required.',file=sys.stderr)
    print(text)

if __name__=='__main__': main()
