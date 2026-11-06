"""CI browser smoke test. Uses the fictional GTFS fixture, not live Sardinian data."""
from __future__ import annotations
import sys
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


def main() -> int:
    opts=Options()
    opts.add_argument('--headless=new')
    opts.add_argument('--no-sandbox')
    opts.add_argument('--disable-dev-shm-usage')
    opts.add_argument('--window-size=1600,900')
    driver=webdriver.Chrome(options=opts)
    try:
        driver.get('http://127.0.0.1:5173/?date=2026-11-06')
        wait=WebDriverWait(driver,35)
        wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR,'.route-item')) >= 3)
        wait.until(lambda d: '3 active routes' in d.find_element(By.CSS_SELECTOR,'.source-strip').text)
        driver.find_element(By.XPATH,"//button[contains(@class,'route-item')][.//strong[text()='N1']]").click()
        wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR,'.inspector .inspect-label'))>0)
        wait.until(lambda d: len(d.find_elements(By.CSS_SELECTOR,'.inspector .departure'))>0)
        assert 'Synthetic overnight' in driver.find_element(By.CSS_SELECTOR,'.inspector').text
        # A live map layer should exist and be populated; this does not validate
        # geographic accuracy or guarantee third-party tiles are available.
        assert driver.execute_script("return document.querySelector('.maplibregl-canvas') !== null")
        driver.find_element(By.XPATH,"//button[contains(.,'Street map') or contains(.,'Plain map')]").click()
        print('UI smoke passed: routes, inspector, timetable, map canvas, basemap toggle')
        return 0
    except Exception:
        Path('ui-smoke-failure.png').write_bytes(driver.get_screenshot_as_png())
        print('UI smoke failed; screenshot written to ui-smoke-failure.png',file=sys.stderr)
        raise
    finally:
        driver.quit()

if __name__=='__main__':
    raise SystemExit(main())
