"""Deterministic local filter races; --expect-stale records the pre-fix RED."""
from __future__ import annotations
import argparse
import traceback
import time
from selenium.webdriver.common.by import By
from browser_checks import (BASE, SOURCES, add_rule, browser, delivered, disable,
    held, inspect, logs, mode, ready, release, screenshot, set_date, source,
    until, write_report)


def empty_or_hidden(state):
    return ((state['network']['featureCount']==0 or not state['network']['visible']) and
            (state['stops']['count']==0 or not state['stops']['visible']) and
            state['network']['renderedFeatureCount']==0 and
            state['stops']['renderedFeatureCount']==0)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--expect-stale',action='store_true')
    args=parser.parse_args()
    print('Launching local Chrome for filter races',flush=True)
    driver=browser();report={'status':'running','checks':[], 'expected_stale':args.expect_stale,'timings_ms':{}}
    started=time.perf_counter()
    try:
        driver.get(BASE+'/?date=2026-11-06&basemap=plain&diagnostics=1')
        print('Local page loaded; waiting for MapLibre baseline',flush=True)
        report['baseline']=ready(driver,'2026-11-06',SOURCES,baseline=args.expect_stale)
        report['timings_ms']['initial_map_ready']=round((time.perf_counter()-started)*1000,1)
        print('Baseline ready; holding date transition responses',flush=True)
        net=add_rule(driver,'/network/geometry','hold','2026-11-08')
        stops=add_rule(driver,'/stops','hold','2026-11-08')
        changed=time.perf_counter();set_date(driver,'2026-11-08')
        if not args.expect_stale:
            until(driver,lambda d:empty_or_hidden(inspect(d)),'old map becomes invisible')
            report['timings_ms']['date_change_to_old_map_hidden']=round((time.perf_counter()-changed)*1000,1)
        held(driver,net,stops)
        pending=inspect(driver);report['held_date_change']=pending
        report['held_screenshot']=screenshot(driver,'race-date-held')
        if args.expect_stale:
            assert not empty_or_hidden(pending), 'Expected pre-fix stale map reproduction'
            assert pending['network']['featureCount']>0 and pending['stops']['count']>0,pending
            report['status']='red-reproduced';return
        assert empty_or_hidden(pending), 'Old network/stops still visible while changed date requests are held'
        report['checks'].append('held date clears or hides both prior map sources')
        # Finish newest Nov 7 first, then deliberately deliver the cancelled Nov 8.
        disable(driver,net,stops);changed=time.perf_counter();set_date(driver,'2026-11-07')
        latest=ready(driver,'2026-11-07',SOURCES)
        report['timings_ms']['new_date_map_ready']=round((time.perf_counter()-changed)*1000,1)
        release(driver,net,stops);delivered(driver,net,stops)
        # Two animation frames flush promise completion and the following React/map turn.
        driver.execute_async_script('const done=arguments[0];requestAnimationFrame(()=>requestAnimationFrame(done))')
        after=ready(driver,'2026-11-07',SOURCES)
        assert after['network']['requestKey']==latest['network']['requestKey']
        assert after['stops']['requestKey']==latest['stops']['requestKey']
        assert after['network']['routeUids']==latest['network']['routeUids']
        report['out_of_order']={'newest':latest,'after_old_release':after}
        report['checks'].append('cancelled older successful responses cannot overwrite latest date')
        # Independent endpoint failures must remove prior visible source data.
        print('Out-of-order dates passed; injecting endpoint failures',flush=True)
        neterr=add_rule(driver,'/network/geometry','error','2026-11-08')
        stoperr=add_rule(driver,'/stops','error','2026-11-08')
        set_date(driver,'2026-11-08')
        failed=until(driver,lambda d:(s if (s:=inspect(d)) and s['network']['status']=='error' and s['stops']['status']=='error' else False),'network and stops explicit error states')
        assert empty_or_hidden(failed),failed
        assert '503' in str(failed['network']['error']),failed
        until(driver,lambda d:'Injected Phase 3 local failure' in d.find_element(By.TAG_NAME,'body').text,'actionable visible failure message')
        report['errors']=failed;report['error_screenshot']=screenshot(driver,'race-errors')
        report['checks'].append('network and stop failures hide prior map results and expose actionable error')
        disable(driver,neterr,stoperr);set_date(driver,'2026-11-06');ready(driver,'2026-11-06',SOURCES)
        print('Failure isolation passed; exercising rapid filter changes',flush=True)
        # Hold every intermediate geometry/stops completion across four control types.
        net=add_rule(driver,'/network/geometry','hold')
        stops=add_rule(driver,'/stops','hold')
        set_date(driver,'2026-11-08');source(driver,'aspo');mode(driver,'Bus')
        active=driver.find_element(By.CSS_SELECTOR,'.sidebar > .check input')
        active.click();source(driver,'trenitalia_sardegna');mode(driver,'Rail')
        held(driver,net,stops)
        pending=inspect(driver);assert empty_or_hidden(pending),pending
        disable(driver,net,stops)
        # All controls end with a new, unheld target.
        set_date(driver,'2026-11-07');final=ready(driver,'2026-11-07',{'trenitalia_sardegna'})
        assert final['filters']['mode']=='rail' and final['filters']['activeOnly'] is False,final
        assert set(final['stops']['sourceIds']) <= {'trenitalia_sardegna'},final
        release(driver,net,stops);delivered(driver,net,stops)
        driver.execute_async_script('const done=arguments[0];requestAnimationFrame(()=>requestAnimationFrame(done))')
        after=ready(driver,'2026-11-07',{'trenitalia_sardegna'})
        assert after['filters']==final['filters'],after
        assert after['network']['requestKey']==final['network']['requestKey'],after
        assert after['network']['routeUids']==final['network']['routeUids'],after
        assert set(after['network']['renderedSourceIds']) <= {'trenitalia_sardegna'},after
        assert set(after['stops']['sourceIds']) <= {'trenitalia_sardegna'},after
        report['rapid_filters']={'newest':final,'after_old_release':after}
        report['checks'].append('rapid date/source/mode/activeOnly changes resist older completions')
        report['final_screenshot']=screenshot(driver,'race-final')
        report['status']='passed'
    except Exception:
        report['status']='failed';report['traceback']=traceback.format_exc()
        report['failure_screenshot']=screenshot(driver,'race-failure')
        try:report['failure_state']=inspect(driver)
        except Exception:pass
        raise
    finally:
        report['javascript_errors']=driver.execute_script('return window.__atlasTest?.errors')
        report['browser_logs']=logs(driver)
        report['intercepted_requests']=driver.execute_script('return window.__atlasTest?.requests.map(({release,...r})=>r)')
        write_report('filter-races-red.json' if args.expect_stale else 'filter-races.json',report)
        driver.quit()

if __name__=='__main__':main()
