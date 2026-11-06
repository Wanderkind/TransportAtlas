"""Shared, condition-driven Selenium checks. All artifacts are new Phase 3 files."""
from __future__ import annotations
import json
from pathlib import Path
import time
from urllib.request import urlopen
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / 'data/local-verification/phase3'
SOURCES = {'arst', 'aspo', 'atp_sassari', 'trenitalia_sardegna'}
BASE = 'http://127.0.0.1:5173'

# Holding happens after the real local response arrives. Ignoring its AbortSignal is
# deliberate: the app must guard stale completions even when cancellation loses.
INTERCEPT = r"""
(() => {
 const nativeFetch = window.fetch.bind(window);
 const state = window.__atlasTest = {rules:[],requests:[],blocked:[],errors:[]};
 window.addEventListener('error',e=>state.errors.push({message:e.message,stack:e.error?.stack,time:performance.now()}));
 state.add = rule => {rule.id = state.rules.length;state.rules.push(rule);return rule.id};
 state.release = id => {state.rules[id].released=true;state.requests.filter(r=>r.rule===id).forEach(r=>r.release?.())};
 window.fetch = async (input, init={}) => {
  const u = new URL(typeof input==='string'?input:input.url,location.href);
  if(u.protocol==='https:'){state.blocked.push(u.href);throw new Error('Offline smoke blocked '+u.href)}
  const rule=state.rules.find(r=>!r.disabled && u.pathname.includes(r.path) && (!r.date||u.searchParams.get('date')===r.date));
  const record={url:u.href,rule:rule?.id,started:performance.now(),delivered:false};state.requests.push(record);
  if(rule?.action==='error'){record.delivered=true;return new Response(JSON.stringify({detail:'Injected Phase 3 local failure'}),{status:503,headers:{'Content-Type':'application/json'}})}
  if(rule?.action==='limit'){u.searchParams.set('limit',String(rule.limit));input=u.href}
  let response=await nativeFetch(input,rule?{...init,signal:undefined}:init);
  if(rule?.action==='hold'){
   record.ready=true;
   if(!rule.released)await new Promise(resolve=>{record.release=resolve});
  }
  if(rule?.action==='warning'){
   const body=await response.json();
   if(body.metadata){body.metadata.truncated=true;body.metadata.warning='Injected incomplete geometry: local verification';body.metadata.missing_geometry_routes=1;}
   if(body.departures){body.truncated=true;body.total_departures=body.departures.length+7;if('matching_departures' in body)body.matching_departures=body.departures.length+7;body.windows_truncated=true;}
   response=new Response(JSON.stringify(body),{status:200,headers:{'Content-Type':'application/json'}});
  }
  record.delivered=true;record.finished=performance.now();return response;
 };
})();
"""

def api(path):
    with urlopen('http://127.0.0.1:8000/api/v1/'+path, timeout=120) as response:
        return json.load(response)

def browser():
    options=webdriver.ChromeOptions()
    options.add_argument('--headless=new')
    options.add_argument('--window-size=1700,1100')
    options.set_capability('goog:loggingPrefs', {'browser':'ALL'})
    driver=webdriver.Chrome(options=options)
    driver.execute_cdp_cmd('Network.enable', {})
    driver.execute_cdp_cmd('Network.setBlockedURLs', {'urls':['https://*']})
    driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {'source':INTERCEPT})
    driver.set_script_timeout(90)
    driver.set_page_load_timeout(90)
    return driver

def inspect(driver):
    result=driver.execute_async_script("""
      const done=arguments[arguments.length-1];
      if(!window.transportAtlasDiagnostics){done(null);return}
      Promise.resolve(window.transportAtlasDiagnostics.inspect()).then(done,e=>done({diagnosticError:String(e)}));
    """)
    if result and result.get('diagnosticError'):
        raise AssertionError(result)
    return result

def until(driver, predicate, message='condition', timeout=120):
    return WebDriverWait(driver, timeout, poll_frequency=.15).until(predicate, message)

def ready(driver, date=None, sources=None, *, baseline=False):
    def match(d):
        s=inspect(d)
        if not s or (date and s['filters']['date']!=date): return False
        n=s['network'];t=s['stops']
        if n['status']!='ready' or t['status']!='ready' or not n['sourceLoaded'] or not t['sourceLoaded']:return False
        if (n['featureCount'] and not n['visible']) or (t['count'] and not t['visible']):return False
        if not baseline and (n['requestKey']!=s['filters']['key'] or t['requestKey']!=s['filters']['key']):return False
        if sources is not None and set(n['sourceIds'])!=set(sources):return False
        return s
    return until(driver, match, 'current MapLibre sources ready')

def set_date(driver, date):
    element=driver.find_element(By.CSS_SELECTOR,'input[aria-label="Service date"]')
    driver.execute_script("Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(arguments[0],arguments[1]);arguments[0].dispatchEvent(new Event('input',{bubbles:true}));arguments[0].dispatchEvent(new Event('change',{bubbles:true}));",element,date)

def set_input(driver, element, value):
    """Set a React-controlled input; Selenium's clear() does not emit React's input event."""
    driver.execute_script("Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(arguments[0],arguments[1]);arguments[0].dispatchEvent(new Event('input',{bubbles:true}));",element,value)

def source(driver, value):
    Select(driver.find_element(By.CSS_SELECTOR,'select[aria-label="Data source"]')).select_by_value(value)

def mode(driver, label):
    driver.find_element(By.XPATH, f"//div[contains(@class,'mode-tabs')]//button[normalize-space()='{label}']").click()

def add_rule(driver, path, action, date=None, **extra):
    return driver.execute_script('return window.__atlasTest.add(arguments[0])',{'path':path,'action':action,'date':date,**extra})

def disable(driver, *ids):
    driver.execute_script('arguments[0].forEach(id=>window.__atlasTest.rules[id].disabled=true)',list(ids))

def release(driver, *ids):
    driver.execute_script('arguments[0].forEach(id=>window.__atlasTest.release(id))',list(ids))

def held(driver, *ids):
    return until(driver,lambda d:d.execute_script('return arguments[0].every(id=>window.__atlasTest.requests.some(r=>r.rule===id&&r.ready))',list(ids)), 'held real responses ready')

def delivered(driver, *ids):
    return until(driver,lambda d:d.execute_script('return arguments[0].every(id=>window.__atlasTest.requests.filter(r=>r.rule===id).length>0&&window.__atlasTest.requests.filter(r=>r.rule===id).every(r=>r.delivered))',list(ids)), 'held responses delivered')

def write_report(name, report):
    EVIDENCE.mkdir(parents=True,exist_ok=True)
    target=EVIDENCE/name
    if target.exists():
        target=target.with_name(f'{target.stem}-{time.time_ns()}{target.suffix}')
    target.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(str(target))
    return target

def screenshot(driver, name):
    EVIDENCE.mkdir(parents=True,exist_ok=True)
    path=EVIDENCE/f'{name}-{time.time_ns()}.png'
    driver.save_screenshot(str(path));return str(path)

def logs(driver):
    return driver.get_log('browser')
