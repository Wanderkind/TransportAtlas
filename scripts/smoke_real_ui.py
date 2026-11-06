"""Local browser/API checks against the selected real feeds; no CI required."""
from __future__ import annotations
import json
from pathlib import Path
import time
from urllib.request import urlopen

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait


def main():
    root = Path('data/local-verification')
    root.mkdir(parents=True, exist_ok=True)
    timings = {}
    def api(path):
        start = time.perf_counter()
        with urlopen('http://127.0.0.1:8000/api/v1/' + path, timeout=120) as response:
            data = json.load(response)
        timings[path] = round((time.perf_counter() - start) * 1000, 1)
        return data
    assert api('health')['database_backend'] == 'duckdb'
    snapshots = api('snapshots')
    assert {'aspo', 'trenitalia_sardegna', 'arst'} <= {s['source_id'] for s in snapshots}
    for day in ('2026-11-06', '2026-11-07', '2026-11-08'):
        api(f'summary?date={day}')
    network = api('network/geometry?date=2026-11-06')
    assert network['features']
    options = webdriver.ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--window-size=1700,1100')
    options.set_capability('goog:loggingPrefs', {'browser': 'ALL'})
    driver = webdriver.Chrome(options=options)
    try:
        start = time.perf_counter()
        driver.get('http://127.0.0.1:5173/?date=2026-11-06&basemap=plain')
        wait = WebDriverWait(driver, 120)
        wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, '.route-item')) > 0)
        wait.until(lambda d: 'Loading' not in d.find_element(By.CSS_SELECTOR, '.app-status').text)
        wait.until(lambda d: d.execute_script("return !!document.querySelector('.maplibregl-canvas')"))
        timings['browser_initial_ready_ms'] = round((time.perf_counter() - start) * 1000, 1)
        resources=driver.execute_script("return performance.getEntriesByType('resource').map(r=>({url:r.name,duration_ms:Math.round(r.duration),bytes:r.transferSize})).sort((a,b)=>b.duration_ms-a.duration_ms).slice(0,12)")
        route = driver.find_element(By.CSS_SELECTOR, '.route-item')
        route.click()
        wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, '.inspector .departure')) > 0)
        driver.find_element(By.CSS_SELECTOR, '.inspector .departure').click()
        wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, '.trip-stop')) >= 2)
        driver.find_element(By.CSS_SELECTOR, '.provenance summary').click()
        assert 'SHA-256' in driver.find_element(By.CSS_SELECTOR, '.inspector').text
        driver.save_screenshot(str(root / 'dashboard.png'))
        search = driver.find_element(By.CSS_SELECTOR, 'input[aria-label="Search stops"]')
        search.send_keys('Olbia')
        wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, '.stop-result')) > 0)
        driver.find_element(By.CSS_SELECTOR, '.stop-result').click()
        wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, '.inspector .inspect-label')) and
                   d.find_element(By.CSS_SELECTOR, '.inspector .inspect-label').text == 'STOP')
        Select(driver.find_element(By.CSS_SELECTOR, 'select[aria-label="Data source"]')).select_by_value('trenitalia_sardegna')
        wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR, '.route-item')) > 0 and all('trenitalia_sardegna' in r.text for r in d.find_elements(By.CSS_SELECTOR, '.route-item')))
        driver.find_element(By.XPATH, "//button[normalize-space()='Tram']").click()
        wait.until(lambda d: 'No matching routes' in d.find_element(By.CSS_SELECTOR, '.route-list').text)
        driver.find_element(By.XPATH, "//button[normalize-space()='All']").click()
        checkbox=driver.find_element(By.CSS_SELECTOR,'input[aria-label="Show trenitalia_sardegna"]')
        checkbox.click()
        wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR,'.route-item')) == 0)
        checkbox.click()
        wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR,'.route-item')) > 0)
        date_input=driver.find_element(By.CSS_SELECTOR,'input[aria-label="Service date"]')
        driver.execute_script("Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(arguments[0],'2026-11-08'); arguments[0].dispatchEvent(new Event('input',{bubbles:true})); arguments[0].dispatchEvent(new Event('change',{bubbles:true}));", date_input)
        wait.until(lambda d: '119 scheduled trips' in d.find_element(By.CSS_SELECTOR,'.source-strip').text)
        driver.find_element(By.XPATH, "//button[contains(.,'Fit')]").click()
        driver.find_element(By.XPATH, "//button[contains(.,'Plain map')]").click()
        driver.save_screenshot(str(root / 'dashboard-interactions.png'))
        errors = [e for e in driver.get_log('browser') if e['level'] == 'SEVERE' and
                  not any(x in e['message'] for x in ('openfreemap', 'favicon.ico'))]
        assert not errors, errors
        report = {'status': 'passed', 'timings_ms': timings, 'network_metadata': network['metadata'],
                  'slowest_initial_resources':resources,
                  'note': 'Browser readiness includes data and canvas; not a GPU frame-time benchmark.'}
        (root / 'browser.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report, indent=2))
    except Exception:
        driver.save_screenshot(str(root / 'browser-failure.png'))
        raise
    finally:
        driver.quit()


if __name__ == '__main__':
    main()
