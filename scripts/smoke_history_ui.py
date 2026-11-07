"""Real-browser checks for the Phase 6 History view (with Phase 7C provenance).

Modes, chosen from what the running backend serves:

* synthetic: backend started on data/history-demo/history_demo.duckdb (see
  scripts/build_history_demo.py). Full comparison workflow. If the demo was built
  with --admin-stage archived, the Phase 7C checks run instead: an invented
  third-party archived copy is labelled as such, observation and local retrieval
  times are shown and sorted separately, and a same-date comparison still works.
* real: the user's four-source database. Each operator has one snapshot, so the
  view must say comparison is impossible, and the atlas must be unchanged.

Evidence goes to ignored data/local-verification/phase3/.
"""
import time
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select
from browser_checks import BASE, SOURCES, add_rule, api, browser, disable, logs, ready, screenshot, set_date, until, write_report

DATE = '2026-11-06'


def text(driver, css):
    return driver.find_element(By.CSS_SELECTOR, css).text


def set_value(driver, label, value):
    element = driver.find_element(By.CSS_SELECTOR, f'input[aria-label="{label}"]')
    driver.execute_script("Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(arguments[0],arguments[1]);"
                          "arguments[0].dispatchEvent(new Event('input',{bubbles:true}));arguments[0].dispatchEvent(new Event('change',{bubbles:true}));",
                          element, value)


def provenance_cells(driver):
    return {e.get_attribute('data-snapshot-id'): (e.find_element(By.CSS_SELECTOR, '[data-field="provenance"]').get_attribute('data-provenance-kind'),
                                                   e.find_element(By.CSS_SELECTOR, '[data-field="provenance"]').text,
                                                   e.find_element(By.CSS_SELECTOR, '[data-field="observed"]').text,
                                                   e.find_element(By.CSS_SELECTOR, '[data-field="retrieved"]').text)
            for e in driver.find_elements(By.CSS_SELECTOR, '.history-table.snapshots tbody tr')}


def snapshot_rows(driver):
    return [(e.get_attribute('data-snapshot-id'), e.get_attribute('data-selected'), e.find_element(By.CSS_SELECTOR, '[data-field="in-coverage"]').text)
            for e in driver.find_elements(By.CSS_SELECTOR, '.history-table.snapshots tbody tr')]


def metric(driver, key):
    row = driver.find_element(By.CSS_SELECTOR, f'.history-table.summary tr[data-metric="{key}"]')
    return row.get_attribute('data-metric-status'), [c.text for c in row.find_elements(By.TAG_NAME, 'td')]


def records(driver, kind):
    return {e.get_attribute('data-record-id'): (e.get_attribute('data-record-status'), e.text)
            for e in driver.find_elements(By.CSS_SELECTOR, f'.record-table[data-kind="{kind}"] tbody tr')}


def run_compare(driver):
    driver.find_element(By.CSS_SELECTOR, '.run-compare').click()


def comparison_ready(driver, baseline, comparison, policy):
    return until(driver, lambda d: d.find_elements(By.CSS_SELECTOR, f'.comparison[data-policy="{policy}"][data-baseline="{baseline}"][data-comparison="{comparison}"]')
                 and d.find_elements(By.CSS_SELECTOR, '.record-table[data-kind="routes"] tbody tr, .record-table[data-kind="routes"] p.muted'), f'{policy} comparison rendered')


def back_to_atlas(driver, selection_before, report):
    driver.find_element(By.CSS_SELECTOR, '.back-to-atlas').click()
    until(driver, lambda d: not d.find_elements(By.CSS_SELECTOR, '.history-view') and d.find_element(By.CSS_SELECTOR, '.workspace').is_displayed(), 'atlas shown again')
    assert 'view=history' not in driver.current_url
    assert [s['snapshot_id'] for s in api('snapshots')] == selection_before, 'selection must not change'
    report['checks'].append('back to atlas keeps the current selection unchanged')


def synthetic(driver, report, sources):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'backend'/'tests'))
    import history_fixtures as hf
    inv = api(f'history/{hf.SOURCE}/snapshots?date={DATE}')
    v1, v2 = [s['snapshot_id'] for s in inv['snapshots']]
    selection_before = [s['snapshot_id'] for s in api('snapshots')]
    # Which version is current depends on how the demo was built (legacy importer or the
    # Phase 7A admin stages); the History view must reflect the API, never assume v2.
    current = next(s['snapshot_id'] for s in api('snapshots') if s['source_id'] == hf.SOURCE)
    flag = {v1: 'true' if current == v1 else 'false', v2: 'true' if current == v2 else 'false'}
    report['current_synthetic_version'] = 'v1' if current == v1 else 'v2'
    driver.get(BASE+f'/?date={DATE}&basemap=plain&diagnostics=1&view=history')
    until(driver, lambda d: snapshot_rows(d) == [(v1, flag[v1], 'Yes'), (v2, flag[v2], 'Yes')], 'snapshot inventory listed')
    assert Select(driver.find_element(By.CSS_SELECTOR, 'select[aria-label="History operator"]')).first_selected_option.get_attribute('value') == hf.SOURCE
    set_value(driver, 'Coverage check date', hf.ONLY_V1_DATE)
    until(driver, lambda d: snapshot_rows(d) == [(v1, flag[v1], 'Yes'), (v2, flag[v2], 'No (service unknown)')], 'coverage follows the check date')
    set_value(driver, 'Coverage check date', DATE)
    until(driver, lambda d: snapshot_rows(d)[1][2] == 'Yes', 'coverage date restored')
    report['checks'].append('inventory lists both synthetic versions, the current-atlas flag and per-date coverage')

    run_compare(driver)
    comparison_ready(driver, v1, v2, 'same_date')
    assert driver.find_elements(By.CSS_SELECTOR, '.synthetic-banner[data-synthetic="true"]')
    status, cells = metric(driver, 'trip_templates_on_date')
    assert status == 'changed' and cells[1:4] == ['8', '9', '+1'] and cells[4] == '+12.5%', cells
    assert metric(driver, 'exact_trip_instances')[1][1:3] == ['9', '10']
    until(driver, lambda d: {k: v[0] for k, v in records(d, 'routes').items()} == {'R10': 'added', 'R2': 'modified', 'R9': 'removed'}, 'route record changes')
    r2 = records(driver, 'routes')['R2'][1]
    assert 'Piazza - Station → Piazza - Station via Quarry' in r2 and '2 → 3' in r2, r2
    until(driver, lambda d: {k: v[0] for k, v in records(d, 'stops').items()} == {'NEW': 'added', 'OLD': 'removed', 'Q': 'modified', 'S': 'modified'}, 'stop record changes')
    assert 'SYNTHETIC Quarry → SYNTHETIC Quarry Gate' in records(driver, 'stops')['Q'][1]
    assert 'not proof' in text(driver, '.identity-note')
    Select(driver.find_element(By.CSS_SELECTOR, 'select[aria-label="routes status filter"]')).select_by_value('all')
    until(driver, lambda d: len(records(d, 'routes')) == 7, 'all route records')
    report['same_date_screenshot'] = screenshot(driver, 'phase6-history-same-date')
    report['checks'].append('same-date comparison: counts, percent of baseline, ID-level added/removed/modified with original values')

    set_value(driver, 'Comparison service date', hf.ONLY_V2_DATE)
    until(driver, lambda d: d.find_elements(By.CSS_SELECTOR, '[data-stale="true"]'), 'stale inputs flagged')
    run_compare(driver)
    until(driver, lambda d: d.find_elements(By.CSS_SELECTOR, '[data-notice="baseline_date_outside_coverage"]'), 'outside-coverage notice')
    status, cells = metric(driver, 'trip_templates_on_date')
    assert status == 'incomparable' and cells[1] == '—' and cells[2] == '9' and 'Not comparable' in cells[3], cells
    assert driver.find_element(By.CSS_SELECTOR, '.side[data-side="baseline"]').get_attribute('data-in-coverage') == 'false'
    report['checks'].append('a date outside one snapshot is not comparable and never shown as zero service')

    driver.find_element(By.CSS_SELECTOR, 'input[aria-label="Different service dates"]').click()
    set_value(driver, 'Baseline service date', hf.ONLY_V1_DATE)
    set_value(driver, 'Comparison service date (second)', hf.ONLY_V2_DATE)
    run_compare(driver)
    comparison_ready(driver, v1, v2, 'different_dates')
    notice = text(driver, '[data-notice="different_dates"]')
    assert 'not attributable to the feed update alone' in notice
    assert 'Feed version and service date comparison' in text(driver, '.comparison h3')
    report['different_dates_screenshot'] = screenshot(driver, 'phase6-history-different-dates')
    report['checks'].append('different-dates policy is labelled as a version and date comparison, not a revision')

    driver.find_element(By.CSS_SELECTOR, 'input[aria-label="Same service date"]').click()
    set_value(driver, 'Comparison service date', DATE)
    Select(driver.find_element(By.CSS_SELECTOR, 'select[aria-label="Comparison snapshot"]')).select_by_value(v1)
    assert 'same snapshot' in text(driver, '.compare-form')
    run_compare(driver)
    until(driver, lambda d: d.find_elements(By.CSS_SELECTOR, '[data-notice="same_snapshot"]'), 'same-snapshot notice')
    # A failed request shows an error with retry, then recovers.
    rule = add_rule(driver, '/history/', 'error')
    Select(driver.find_element(By.CSS_SELECTOR, 'select[aria-label="Comparison snapshot"]')).select_by_value(v2)
    set_value(driver, 'Comparison service date', '2026-11-09')
    run_compare(driver)
    until(driver, lambda d: d.find_elements(By.CSS_SELECTOR, '.history-error') and 'Injected' in text(d, '.history-error'), 'comparison error shown')
    disable(driver, rule)
    driver.find_element(By.CSS_SELECTOR, '.history-error button').click()
    comparison_ready(driver, v1, v2, 'same_date')
    report['checks'].append('equal snapshots are flagged; request errors are shown and retryable')

    Select(driver.find_element(By.CSS_SELECTOR, 'select[aria-label="History operator"]')).select_by_value(hf.OTHER_SOURCE)
    until(driver, lambda d: d.find_elements(By.CSS_SELECTOR, '[data-comparison-possible="false"]') and not d.find_elements(By.CSS_SELECTOR, '.compare-form'), 'single-snapshot operator explained')
    report['checks'].append('one-snapshot operator: comparison explained as impossible, no form')
    back_to_atlas(driver, selection_before, report)
    ready(driver, DATE, set(sources) - {'none'})


def archived(driver, report, sources):
    """Phase 7C: an invented archived copy of the OLDER version, obtained after the current one."""
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'backend'/'tests'))
    import history_fixtures as hf
    observed = api(f'history/{hf.SOURCE}/snapshots?date={DATE}&sort=observed')['snapshots']
    retrieved = api(f'history/{hf.SOURCE}/snapshots?date={DATE}&sort=retrieved')['snapshots']
    old, new = [s['snapshot_id'] for s in observed]
    assert [s['snapshot_id'] for s in retrieved] == [new, old], 'archived copy was obtained after the current one'
    assert [s['provenance']['kind'] for s in observed] == ['third_party_archived', 'publisher_direct']
    selection_before = [s['snapshot_id'] for s in api('snapshots')]
    assert next(s['snapshot_id'] for s in api('snapshots') if s['source_id'] == hf.SOURCE) == new
    report['current_synthetic_version'] = 'archived'
    driver.get(BASE+f'/?date={DATE}&basemap=plain&diagnostics=1&view=history')
    until(driver, lambda d: snapshot_rows(d) == [(old, 'false', 'Yes'), (new, 'true', 'Yes')], 'observation-ordered inventory')
    cells = provenance_cells(driver)
    kind, label, seen, got = cells[old]
    assert kind == 'third_party_archived' and 'Archived copy' in label and 'SYNTHETIC Example Archive' in label, cells[old]
    assert 'not downloaded from the publisher by this computer' in label and 'CC-BY-4.0' in label, label
    assert seen.startswith('2026-08-28') and 'per the archive record' in seen, seen
    assert got[:10] == observed[0]['retrieved_at'][:10] and got[:10] != '2026-08-28', got
    assert cells[new][0] == 'publisher_direct' and 'our own download' in cells[new][2], cells[new]
    assert 'observation time' in text(driver, '.sort-note').lower()
    report['checks'].append('archived copy labelled with its archive, publisher, licence; archive observation and local retrieval shown separately')
    Select(driver.find_element(By.CSS_SELECTOR, 'select[aria-label="Snapshot order"]')).select_by_value('retrieved')
    until(driver, lambda d: [r[0] for r in snapshot_rows(d)] == [new, old], 'retrieval-ordered inventory')
    assert 'local retrieval time' in text(driver, '.sort-note')
    report['sort_screenshot'] = screenshot(driver, 'phase7c-history-retrieval-order')
    Select(driver.find_element(By.CSS_SELECTOR, 'select[aria-label="Snapshot order"]')).select_by_value('observed')
    until(driver, lambda d: [r[0] for r in snapshot_rows(d)] == [old, new], 'observation order restored')
    report['checks'].append('the two orders differ and are labelled; neither is presented as publication chronology')
    run_compare(driver)
    comparison_ready(driver, old, new, 'same_date')
    assert driver.find_elements(By.CSS_SELECTOR, '.synthetic-banner[data-synthetic="true"]')
    notice = text(driver, '[data-notice="baseline_third_party_archived"]')
    assert 'SYNTHETIC Example Archive' in notice and 'not downloaded from the publisher' in notice and '2026-08-28' in notice, notice
    side = driver.find_element(By.CSS_SELECTOR, '.side[data-side="baseline"] [data-field="side-provenance"]')
    assert side.get_attribute('data-provenance-kind') == 'third_party_archived' and 'observed 2026-08-28' in side.text, side.text
    status, cells = metric(driver, 'trip_templates_on_date')
    assert status == 'changed' and cells[1:4] == ['8', '9', '+1'], cells
    report['archived_screenshot'] = screenshot(driver, 'phase7c-history-archived-compare')
    report['checks'].append('same-date comparison of an archived older copy with the current version, with an attribution notice')
    back_to_atlas(driver, selection_before, report)
    ready(driver, DATE, set(sources) - {'none'})


def real(driver, report):
    selection_before = [s['snapshot_id'] for s in api('snapshots')]
    driver.get(BASE+f'/?date={DATE}&basemap=plain&diagnostics=1')
    ready(driver, DATE, SOURCES)
    driver.find_element(By.CSS_SELECTOR, '.history-toggle').click()
    until(driver, lambda d: d.find_elements(By.CSS_SELECTOR, '.history-view'), 'history view')
    seen = {}
    for source in sorted(SOURCES):
        Select(driver.find_element(By.CSS_SELECTOR, 'select[aria-label="History operator"]')).select_by_value(source)
        until(driver, lambda d: len(snapshot_rows(d)) == 1 and d.find_elements(By.CSS_SELECTOR, f'.history-table.snapshots tr[data-snapshot-id] code'), f'{source} inventory')
        expected = next(s['snapshot_id'] for s in api('snapshots') if s['source_id'] == source)
        until(driver, lambda d: snapshot_rows(d) == [(expected, 'true', 'Yes')], f'{source} single current snapshot')
        note = text(driver, '[data-comparison-possible="false"]')
        assert 'no version comparison is possible' in note and not driver.find_elements(By.CSS_SELECTOR, '.compare-form')
        kind = provenance_cells(driver)[expected][0]
        assert kind == 'publisher_direct', (source, kind)
        seen[source] = expected
    report['real_sources'] = seen
    report['checks'].append('each real operator shows its single snapshot (publisher download) and that comparison is not possible')
    report['real_screenshot'] = screenshot(driver, 'phase6-history-real')
    back_to_atlas(driver, selection_before, report)
    state = ready(driver, DATE, SOURCES)
    assert state['network']['routeCount'] == 349, state['network']


def main():
    report = {'status': 'running', 'checks': []}
    sources = {s['source_id'] for s in api('history/sources')['sources']}
    report['mode'] = 'synthetic' if 'synthetic_valley' in sources else 'real'
    print(f"Launching local Chrome for History checks ({report['mode']} database)", flush=True)
    driver = browser()
    try:
        started = time.perf_counter()
        if report['mode'] == 'synthetic':
            kinds = {s['provenance']['kind'] for s in api(f'history/synthetic_valley/snapshots?date={DATE}')['snapshots']}
            (archived if 'third_party_archived' in kinds else synthetic)(driver, report, sources)
        else:
            real(driver, report)
        report['elapsed_ms'] = round((time.perf_counter()-started)*1000)
        report['browser_logs'] = logs(driver)
        severe = [e for e in report['browser_logs'] if e['level'] == 'SEVERE' and 'favicon.ico' not in e['message']
                  and 'Injected' not in e['message'] and '503' not in e['message']]
        assert not severe, severe
        report['status'] = 'passed'
    except Exception as exc:
        report.update(status='failed', error=repr(exc))
        try:
            report['failure_screenshot'] = screenshot(driver, 'phase6-history-failure')
        except Exception:
            pass
        raise
    finally:
        write_report(f"phase6-history-{report['mode']}{'-'+report['current_synthetic_version'] if report.get('current_synthetic_version') else ''}.json", report)
        driver.quit()


if __name__ == '__main__':
    main()
