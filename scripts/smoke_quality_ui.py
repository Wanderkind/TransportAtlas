"""Real-browser checks for Phase 5 data-quality indicators against the local API.

Run with services started (scripts/start_local.ps1 -Production). Compares what
the inspectors actually render with the API, and checks that the map gains no
quality markers. Evidence goes to ignored data/local-verification/phase3/.
"""
from selenium.webdriver.common.by import By
from browser_checks import (BASE, SOURCES, api, browser, inspect, logs, ready, screenshot, set_input,
                            until, write_report)

DATE = '2026-11-06'


def inspector_text(driver):
    return driver.find_element(By.CSS_SELECTOR, '.inspector').text


def codes(driver, scope='.inspector'):
    return [e.get_attribute('data-quality-code') for e in driver.find_elements(By.CSS_SELECTOR, f'{scope} [data-quality-code]')]


def open_source(driver, source_id):
    next(e for e in driver.find_elements(By.CSS_SELECTOR, 'button.source-row') if e.get_attribute('data-source-id') == source_id).click()
    until(driver, lambda d: d.find_elements(By.CSS_SELECTOR, f'.inspector .inspect-label[data-source-id="{source_id}"]')
          and d.find_elements(By.CSS_SELECTOR, '.inspector .freshness'), f'{source_id} source inspector')


def main():
    print('Launching local Chrome for data-quality checks', flush=True)
    report = {'status': 'running', 'checks': []}
    driver = browser()
    try:
        snaps = {s['source_id']: s for s in api('snapshots')}
        driver.get(BASE+f'/?date={DATE}&basemap=plain&diagnostics=1')
        state = ready(driver, DATE, SOURCES)
        active_stops = api(f'stops?limit=20000&date={DATE}&active_only=true')['total']
        assert state['stops']['count'] == active_stops, (state['stops'], active_stops)
        assert state['network']['routeCount'] == 349, state['network']
        report['checks'].append('map keeps all 349 routes and exactly the active stops; no quality markers added')

        # Source inspector: freshness fields kept apart; Trenitalia projection caution.
        tq = api(f"quality/source/{snaps['trenitalia_sardegna']['snapshot_id']}?date={DATE}")
        open_source(driver, 'trenitalia_sardegna')
        until(driver, lambda d: sorted(codes(d)) == sorted(i['code'] for i in tq['indicators']), 'Trenitalia indicators match API')
        cell = lambda d, f: d.find_element(By.CSS_SELECTOR, f'.inspector .freshness [data-field="{f}"]').text
        assert tq['freshness']['retrieved_at'] in cell(driver, 'retrieved') and 'not a publication date' in cell(driver, 'retrieved')
        assert cell(driver, 'publication').startswith('Unknown')
        assert '2026-06-05' in cell(driver, 'version') and '2026-06-06 – 2026-12-12' in cell(driver, 'declared')
        assert '200 trip templates scheduled' in cell(driver, 'selected') and '310 on 2026-06-12' in cell(driver, 'selected')
        caution = driver.find_element(By.CSS_SELECTOR, '.inspector [data-quality-code="long_range_reduced_volume"]')
        assert caution.get_attribute('data-level') == 'caution' and 'does not show missing service' in caution.text
        assert 'does not show that the schedules match' in inspector_text(driver)
        report['trenitalia_source'] = tq['freshness']
        report['source_screenshot'] = screenshot(driver, 'phase5-source-trenitalia')
        report['checks'].append('source inspector separates retrieval, version label, publication, declared, observed and date volume')

        aq = api(f"quality/source/{snaps['atp_sassari']['snapshot_id']}?date={DATE}")
        open_source(driver, 'atp_sassari')
        until(driver, lambda d: sorted(codes(d)) == sorted(i['code'] for i in aq['indicators']), 'ATP indicators match API')
        assert 'no_feed_info' in codes(driver) and 'long_range_reduced_volume' not in codes(driver)
        assert cell(driver, 'version') == 'Unknown (no feed_info.txt)'
        report['checks'].append('missing feed_info is labelled unknown; no seasonal caution without a dated label')

        # Route and trip timing indicators (ASPO route 08, trip 216871).
        aspo = snaps['aspo']['snapshot_id']
        rq = api(f'quality/route/{aspo}/08')
        driver.find_element(By.CSS_SELECTOR, f'[data-route-uid="{aspo}:08"]').click()
        route_scope = '.inspector section[aria-label="Route data quality"]'
        until(driver, lambda d: sorted(codes(d, route_scope)) == sorted(i['code'] for i in rq['indicators']), 'route indicators match API')
        note = driver.find_element(By.CSS_SELECTOR, f'{route_scope} [data-quality-code="implausible_stop_intervals"]').text
        assert f"{rq['timing']['trip_templates_with_implausible']} of {rq['timing']['trip_templates_checked']}" in note, note
        assert 'not proof of an error' in note and 'Other trips are not affected' in note
        trip = api(f'trip/{aspo}/216871')
        until(driver, lambda d: [e for e in d.find_elements(By.CSS_SELECTOR, '.inspector button.departure') if e.get_attribute('data-trip-id') == '216871'], 'trip departure listed')[0].click()
        until(driver, lambda d: d.find_elements(By.CSS_SELECTOR, '.trip-stops[data-trip-id="216871"]'), 'trip stops')
        flagged = driver.find_elements(By.CSS_SELECTOR, '.trip-stop.flagged')
        assert len(flagged) == len({f['to_sequence'] for f in trip['timing']['findings']}) == 9, len(flagged)
        assert 'Arr 06:57:01' in inspector_text(driver), 'published seconds must be visible'
        assert 'Times are shown exactly as published' in driver.find_element(By.CSS_SELECTOR, '.trip-timing').text
        report['aspo_route'] = {k: rq['timing'][k] for k in ('trip_templates_checked', 'trip_templates_with_implausible', 'implausible_segments')}
        report['trip_screenshot'] = screenshot(driver, 'phase5-trip-flags')
        report['checks'].append('route and trip intervals flagged contextually with published values unchanged')

        # Stop status: reach an unused ATP stop record through a served neighbour's nearby list.
        atp = snaps['atp_sassari']['snapshot_id']
        unused = 'ATPSS_10'
        around = api(f'stop/{atp}/{unused}/nearby?radius=200&date={DATE}')
        assert around['origin']['service']['status'] == 'no_trip_references'
        served = next(s for s in around['stops'] if s['service']['status'] == 'scheduled' and s['snapshot_id'] == atp)
        search = driver.find_element(By.CSS_SELECTOR, 'input[aria-label="Search stops"]')
        set_input(driver, search, served['stop_id'])
        until(driver, lambda d: [e for e in d.find_elements(By.CSS_SELECTOR, '.stop-result') if e.get_attribute('data-stop-id') == served['stop_id']], 'served neighbour found')[0].click()
        until(driver, lambda d: d.find_elements(By.CSS_SELECTOR, '.inspector .stop-service[data-service-status="scheduled"]'), 'served stop status')
        near = api(f"stop/{atp}/{served['stop_id']}/nearby?radius=200&date={DATE}")
        expected = [(n['source_id'], n['stop_id'], n['service']['status']) for n in near['stops']]
        until(driver, lambda d: [(e.get_attribute('data-source-id'), e.get_attribute('data-stop-id'),
                                  e.find_element(By.CSS_SELECTOR, '.service-status').get_attribute('data-service-status'))
                                 for e in d.find_elements(By.CSS_SELECTOR, '.inspector .nearby-stop')] == expected, 'nearby statuses match API')
        nearby_text = driver.find_element(By.CSS_SELECTOR, '.inspector .nearby').text
        assert 'Not a verified' in nearby_text and 'listed, not hidden' in nearby_text
        report['nearby_statuses'] = expected
        next(e for e in driver.find_elements(By.CSS_SELECTOR, '.inspector .nearby-stop') if e.get_attribute('data-stop-id') == unused).click()
        until(driver, lambda d: d.find_elements(By.CSS_SELECTOR, '.inspector .stop-service[data-service-status="no_trip_references"]'), 'unused stop status')
        until(driver, lambda d: codes(d, '.inspector section[aria-label="Stop data quality"]') == ['stop_without_trips'], 'unused stop indicator')
        assert unused in inspector_text(driver)
        report['stop_screenshot'] = screenshot(driver, 'phase5-unused-stop')
        report['checks'].append('nearby records disclose service status; unused records are labelled, kept, and source IDs shown')

        state = inspect(driver)
        assert state['stops']['count'] == active_stops and state['network']['routeCount'] == 349, state
        report['browser_logs'] = logs(driver)
        severe = [e for e in report['browser_logs'] if e['level'] == 'SEVERE' and 'favicon.ico' not in e['message']]
        assert not severe, severe
        report['status'] = 'passed'
    except Exception as exc:
        report.update(status='failed', error=repr(exc))
        try:
            report['failure_screenshot'] = screenshot(driver, 'phase5-quality-failure')
        except Exception:
            pass
        raise
    finally:
        write_report('phase5-quality-ui.json', report)
        driver.quit()


if __name__ == '__main__':
    main()
