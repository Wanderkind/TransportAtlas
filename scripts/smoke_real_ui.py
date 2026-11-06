"""All-four-source, offline local browser verification. Never imports feed data."""
from __future__ import annotations
import time
import traceback
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from browser_checks import (BASE, SOURCES, add_rule, api, browser, disable,
    inspect, logs, mode, ready, screenshot, set_date, set_input, source, until, write_report)


def clock(seconds):
    return f'{seconds//3600:02}:{seconds%3600//60:02}' if seconds is not None else '—'


def main():
    print('Launching local Chrome for all-four-source smoke',flush=True)
    report={'status':'running','sources':{},'checks':[]};driver=browser()
    try:
        assert api('health')['database_backend']=='duckdb'
        snapshots=api('snapshots');assert SOURCES <= {s['source_id'] for s in snapshots}
        summary=api('summary?date=2026-11-06')
        expected={'arst':(322,3450,4731,286),'aspo':(15,422,319,15),
                  'atp_sassari':(50,1032,1023,46),'trenitalia_sardegna':(2,42,200,2)}
        for row in summary['snapshots']:
            if row['source_id'] in expected:
                assert tuple(row[k] for k in ('total_route_records','total_stop_records','scheduled_trip_instances','active_route_records'))==expected[row['source_id']],row
        network=api('network/geometry?date=2026-11-06')
        report['network_metadata']=network['metadata']
        coverage=network['metadata']['coverage']
        for name,value in {'total_route_records':389,'routes_with_trip_references':350,
                           'active_routes':349,'eligible_routes':349,'rendered_routes':349,
                           'routes_without_trip_references':39,'routes_with_insufficient_geometry':0,
                           'routes_omitted_by_limits':0}.items():
            assert coverage[name]==value,(name,coverage)
        started=time.perf_counter()
        driver.get(BASE+'/?date=2026-11-06&basemap=plain&diagnostics=1')
        state=ready(driver,'2026-11-06',SOURCES)
        until(driver,lambda d: set(inspect(d)['network']['renderedSourceIds'])==SOURCES,'all four operators actually rendered')
        report['initial_ms']=round((time.perf_counter()-started)*1000,1)
        assert state['network']['routeCount']==349,state
        assert set(state['stops']['sourceIds'])==SOURCES,state
        report['all_sources']=inspect(driver);report['overview_screenshot']=screenshot(driver,'all-four-overview')
        # Each operator has a real loaded MapLibre source and exact-trip inspection.
        routes=api('routes?date=2026-11-06')
        if isinstance(routes,dict):routes=routes['routes']
        for operator in sorted(SOURCES):
            print('Inspecting '+operator,flush=True)
            source(driver,operator);state=ready(driver,'2026-11-06',{operator})
            assert state['network']['routeCount']==expected[operator][3],state
            assert state['network']['featureCount']>0 and state['network']['visible'],state
            state=until(driver,lambda d:(s if set((s:=inspect(d))['network']['renderedSourceIds'])=={operator} else False),'selected operator actually rendered')
            assert set(state['network']['renderedSourceIds']) == {operator},state
            assert set(state['stops']['sourceIds'])=={operator},state
            r=next(r for r in routes if r['source_id']==operator and r['active_on_date'])
            uid=f"{r['snapshot_id']}:{r['route_id']}"
            target=until(driver,lambda d:next((e for e in d.find_elements(By.CSS_SELECTOR,'.route-item') if e.get_attribute('data-route-uid')==uid),None),'stable route identifier')
            driver.execute_script('arguments[0].click()',target)
            until(driver,lambda d:len(d.find_elements(By.CSS_SELECTOR,'select[aria-label="Direction"]'))>0,'route timetable')
            timetable=api(f"route/{r['snapshot_id']}/{r['route_id']}/timetable?date=2026-11-06&limit=200")
            v=next(v for v in timetable['variants'] if v['scheduled_departures']>0)
            direction=str(v['direction_id']) if v['direction_id'] is not None else 'null'
            Select(driver.find_element(By.CSS_SELECTOR,'select[aria-label="Direction"]')).select_by_value(direction)
            Select(driver.find_element(By.CSS_SELECTOR,'select[aria-label="Variant"]')).select_by_value(v['variant_id'])
            until(driver,lambda d:(s if (s:=inspect(d))['focused']['featureCount']>0 and s['focused']['sourceLoaded'] else False),'variant focused geometry')
            departure=next(t for t in timetable['departures'] if t['variant_id']==v['variant_id'] and str(t['direction_id'] if t['direction_id'] is not None else 'null')==direction)
            trip=api(f"trip/{r['snapshot_id']}/{departure['trip_id']}")
            # Filtered departures are fetched from the server after the selection.
            until(driver,lambda d:[e for e in d.find_elements(By.CSS_SELECTOR,'.inspector button.departure') if e.get_attribute('data-variant-id')==v['variant_id']],'server-filtered variant departures')[0].click()
            until(driver,lambda d:len(d.find_elements(By.CSS_SELECTOR,'.trip-stop'))==len(trip['stops']),'exact ordered trip stops')
            offset=departure['departure_seconds']-departure['template_first_seconds']
            rows=driver.find_elements(By.CSS_SELECTOR,'.trip-stop')
            for element,stop in zip(rows,trip['stops']):
                # Browsers collapse HTML whitespace (e.g. ATP's 'Via  Baldedda'); compare normalized text.
                assert ' '.join((stop['name'] or stop['stop_id']).split()) in ' '.join(element.text.split()),(element.text,stop)
                for key,label in [('arrival_sec','Arr'),('departure_sec','Dep')]:
                    seconds=stop[key]+offset if stop[key] is not None else None
                    assert f'{label} {clock(seconds)}' in element.text,(element.text,stop)
            focused=until(driver,lambda d:(s if (s:=inspect(d))['focused']['sourceLoaded'] and s['focused']['pointCount']==sum(len(f['geometry']['coordinates']) for f in trip['geometry']['features']) else False),'exact source geometry points')
            assert focused['focused']['visible'] and focused['focused']['featureCount']==len(trip['geometry']['features']),focused
            assert focused['focused']['quality']==trip['geometry']['features'][0]['properties']['quality'],focused
            driver.find_element(By.CSS_SELECTOR,'.provenance summary').click()
            assert 'SHA-256' in driver.find_element(By.CSS_SELECTOR,'.inspector').text
            report['sources'][operator]={'network':state,'focused':focused,'route_uid':uid,'direction':direction,'variant':v['variant_id'],'trip_id':departure['trip_id'],'ordered_stops':len(rows),'screenshot':screenshot(driver,'operator-'+operator)}
            # Board selected through an actual trip stop, with local-date API count.
            first=trip['stops'][0]
            board=api(f"stop/{r['snapshot_id']}/{first['stop_id']}/departures?date=2026-11-06")
            driver.find_element(By.CSS_SELECTOR,'.trip-stop button').click()
            until(driver,lambda d:f"{len(board['departures'])} exact departures on local date 2026-11-06" in d.find_element(By.CSS_SELECTOR,'.inspector').text,'local stop board')
            assert len(driver.find_elements(By.CSS_SELECTOR,'.inspector .departure'))==len(board['departures'])
        report['checks'].append('each operator: real source counts, direction, variant, exact ordered times, original geometry, provenance, stop board')
        # Operator checkbox must remove both source data and actual rendered features.
        source(driver,'');ready(driver,'2026-11-06',SOURCES)
        # Pagination: real routes peak below one 200-row page, so the request page
        # size is rewritten to 50 in the browser; the real server pages the data.
        size=50
        busiest=max((r for r in routes if r['active_on_date'] and r['source_id']=='arst'),key=lambda r:api(f"route/{r['snapshot_id']}/{r['route_id']}/timetable?date=2026-11-06&limit=1")['total_departures'])
        pages=[api(f"route/{busiest['snapshot_id']}/{busiest['route_id']}/timetable?date=2026-11-06&limit={size}&offset={o}") for o in (0,size)]
        total=pages[0]['total_departures'];assert total>size,total
        paging=add_rule(driver,'/timetable','limit','2026-11-06',limit=size)
        uid=f"{busiest['snapshot_id']}:{busiest['route_id']}"
        driver.execute_script('arguments[0].click()',until(driver,lambda d:next((e for e in d.find_elements(By.CSS_SELECTOR,'.route-item') if e.get_attribute('data-route-uid')==uid),None),'busiest route'))
        until(driver,lambda d:len(d.find_elements(By.CSS_SELECTOR,'.inspector button.departure'))==size,'first timetable page')
        assert f'Showing {size} of {total} matching departures' in driver.find_element(By.CSS_SELECTOR,'.inspector').text
        driver.find_element(By.CSS_SELECTOR,'.inspector button.load-more').click()
        expected=[(t['trip_id'],str(t['departure_seconds'])) for p in pages for t in p['departures']]
        until(driver,lambda d:len(d.find_elements(By.CSS_SELECTOR,'.inspector button.departure'))==len(expected),'second timetable page appended')
        shown=[(e.get_attribute('data-trip-id'),e.get_attribute('data-departure-seconds')) for e in driver.find_elements(By.CSS_SELECTOR,'.inspector button.departure')]
        assert shown==expected,(shown[:5],expected[:5])
        # A direction filter is applied by the server to the complete list.
        direction=str(pages[0]['departures'][0]['direction_id'])
        Select(driver.find_element(By.CSS_SELECTOR,'select[aria-label="Direction"]')).select_by_value(direction)
        filtered=api(f"route/{busiest['snapshot_id']}/{busiest['route_id']}/timetable?date=2026-11-06&limit={size}&direction={direction}")
        until(driver,lambda d:[e.get_attribute('data-trip-id') for e in d.find_elements(By.CSS_SELECTOR,'.inspector button.departure')]==[t['trip_id'] for t in filtered['departures']],'server-filtered direction page')
        disable(driver,paging)
        report['pagination']={'route_uid':uid,'total_departures':total,'page_size':size,'shown_after_load_more':len(shown),'direction':direction,'direction_matching':filtered['matching_departures']}
        report['checks'].append('timetable pages append in exact server order; direction filter applied server-side')
        route_search=driver.find_element(By.CSS_SELECTOR,'input[aria-label="Search routes"]')
        route_search.send_keys('atp_sassari')
        until(driver,lambda d:len(d.find_elements(By.CSS_SELECTOR,'.route-item'))==46 and all('atp_sassari' in e.text for e in d.find_elements(By.CSS_SELECTOR,'.route-item')),'route search scoped to ATP')
        set_input(driver,route_search,'')
        until(driver,lambda d:len(d.find_elements(By.CSS_SELECTOR,'.route-item'))>46,'route search cleared')
        stop_search=driver.find_element(By.CSS_SELECTOR,'input[aria-label="Search stops"]');stop_search.send_keys('Olbia')
        until(driver,lambda d:len(d.find_elements(By.CSS_SELECTOR,'.stop-result'))>0,'imported Olbia stop search')
        driver.find_element(By.CSS_SELECTOR,'.stop-result').click()
        until(driver,lambda d:len(d.find_elements(By.CSS_SELECTOR,'.inspector .inspect-label'))>0 and d.find_element(By.CSS_SELECTOR,'.inspector .inspect-label').text=='STOP','searched stop inspection')
        # Nearby stop records across sources must match the API exactly.
        searched=driver.execute_script("return document.querySelector('.inspector h2').textContent")
        olbia=next(s for s in api('stops?date=2026-11-06&active_only=true&search=Olbia')['stops'] if (s['name'] or s['stop_id'])==searched)
        near=api(f"stop/{olbia['snapshot_id']}/{olbia['stop_id']}/nearby?radius=200")
        until(driver,lambda d:[(e.get_attribute('data-source-id'),e.get_attribute('data-stop-id')) for e in d.find_elements(By.CSS_SELECTOR,'.inspector .nearby-stop')]==[(n['source_id'],n['stop_id']) for n in near['stops']] and 'Not a verified' in d.find_element(By.CSS_SELECTOR,'.inspector .nearby').text,'nearby stop records')
        report['nearby']={'stop':olbia['stop_id'],'source':olbia['source_id'],'total':near['total'],'other_sources':sorted({n['source_id'] for n in near['stops'] if not n['same_source']})}
        report['checks'].append('nearby cross-source stop records match the API and are labelled as proximity only')
        set_input(driver,stop_search,'')
        until(driver,lambda d:not d.find_elements(By.CSS_SELECTOR,'.stop-result'),'stop search cleared')
        checkbox=driver.find_element(By.CSS_SELECTOR,'input[aria-label="Show atp_sassari"]');checkbox.click()
        hidden=ready(driver,'2026-11-06',SOURCES-{'atp_sassari'})
        until(driver,lambda d:'atp_sassari' not in inspect(d)['network']['renderedSourceIds'],'hidden operator absent from rendered lines')
        assert 'atp_sassari' not in hidden['stops']['sourceIds'],hidden
        checkbox.click();ready(driver,'2026-11-06',SOURCES)
        source(driver,'trenitalia_sardegna');mode(driver,'Tram')
        empty=ready(driver,'2026-11-06',set());assert empty['network']['featureCount']==0 and empty['stops']['count']==0,empty
        mode(driver,'All');ready(driver,'2026-11-06',{'trenitalia_sardegna'})
        set_date(driver,'2026-11-08');ready(driver,'2026-11-08',{'trenitalia_sardegna'})
        until(driver,lambda d:'119 scheduled trips' in d.find_element(By.CSS_SELECTOR,'.source-strip').text,'Sunday train services')
        report['checks'].append('operator visibility, empty mode, Sunday train date counts')
        # Warning responses are injected locally, without altering feed/database data.
        warning=add_rule(driver,'/network/geometry','warning','2026-11-09')
        twarning=add_rule(driver,'/timetable','warning','2026-11-09')
        set_date(driver,'2026-11-09');ready(driver,'2026-11-09',{'trenitalia_sardegna'})
        until(driver,lambda d:'Injected incomplete geometry' in d.find_element(By.TAG_NAME,'body').text,'incomplete geometry warning')
        # ready() covers map sources only; the date's route catalog loads independently.
        until(driver,lambda d:d.find_elements(By.CSS_SELECTOR,'.route-item'),'route catalog for warning date')[0].click()
        until(driver,lambda d:'matching departures. Summary counts cover the full route' in d.find_element(By.CSS_SELECTOR,'.inspector').text,'timetable truncation warning')
        report['warning_screenshot']=screenshot(driver,'incomplete-and-truncated-warning')
        disable(driver,warning,twarning)
        resources=driver.execute_script("return performance.getEntriesByType('resource').map(r=>({url:r.name,duration_ms:Math.round(r.duration),bytes:r.transferSize}))")
        assert not [r for r in resources if r['url'].startswith('https:')],resources
        blocked=driver.execute_script('return window.__atlasTest.blocked')
        assert not blocked,blocked
        report['offline']={'https_blocked_by_cdp':True,'attempted_https_fetches':blocked,'resources':resources}
        report['checks'].append('plain map works with HTTPS blocked; incomplete/truncated warnings visible')
        report['browser_logs']=logs(driver)
        severe=[e for e in report['browser_logs'] if e['level']=='SEVERE' and 'favicon.ico' not in e['message']]
        assert not severe,severe
        report['status']='passed'
    except Exception:
        report['status']='failed';report['traceback']=traceback.format_exc();report['failure_screenshot']=screenshot(driver,'smoke-failure')
        try:report['failure_state']=inspect(driver)
        except Exception:pass
        report['browser_logs']=logs(driver)
        raise
    finally:
        report['javascript_errors']=driver.execute_script('return window.__atlasTest?.errors')
        write_report('browser.json',report);driver.quit()

if __name__=='__main__':main()
