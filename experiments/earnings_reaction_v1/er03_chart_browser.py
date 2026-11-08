"""Actual local Chrome/CDP checks. No external browser or market requests.

Requires installed Chrome and websocket-client (existing local test dependency).
"""
from __future__ import annotations
import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.request

from .er03_chart_upgrade import OUT, digest, write_json


class Chrome:
    def __enter__(self):
        import websocket
        self.profile=tempfile.TemporaryDirectory(prefix='er03c-chrome-')
        self.proc=subprocess.Popen([
            '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
            '--headless=new','--disable-gpu','--no-first-run','--no-default-browser-check',
            '--disable-background-networking','--disable-component-update','--disable-sync',
            '--metrics-recording-only','--disable-features=MediaRouter,OptimizationHints',
            '--remote-debugging-port=0','--user-data-dir='+self.profile.name,'about:blank'],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        portfile=Path(self.profile.name)/'DevToolsActivePort'
        deadline=time.monotonic()+15
        while not portfile.exists():
            if self.proc.poll() is not None: raise RuntimeError('Chrome exited; sandbox may require escalation')
            if time.monotonic()>deadline: raise TimeoutError('Chrome startup')
            time.sleep(.1)
        self.port=portfile.read_text().splitlines()[0]
        with urllib.request.urlopen('http://127.0.0.1:'+self.port+'/json/list') as f:
            page=next(p for p in json.load(f) if p['type']=='page')
        self.ws=websocket.create_connection(page['webSocketDebuggerUrl'],timeout=15,suppress_origin=True)
        self.seq=0; self.errors=[]; self.remote=[]
        self.call('Runtime.enable');self.call('Page.enable');self.call('Network.enable')
        self.call('Network.setBlockedURLs',{'urls':['http://*','https://*']})
        self.call('Emulation.setDeviceMetricsOverride',dict(width=1480,height=1440,deviceScaleFactor=1,mobile=False))
        return self

    def call(self,method,params=None):
        self.seq+=1;seq=self.seq
        self.ws.send(json.dumps(dict(id=seq,method=method,params=params or {})))
        while True:
            r=json.loads(self.ws.recv())
            if r.get('method')=='Runtime.exceptionThrown': self.errors.append(r['params'])
            if r.get('method')=='Network.requestWillBeSent':
                url=r['params']['request']['url']
                if url.startswith(('http:','https:')): self.remote.append(url)
            if r.get('id')==seq:
                if 'error' in r: raise RuntimeError(r['error'])
                return r.get('result',{})

    def js(self,expr):
        r=self.call('Runtime.evaluate',dict(expression=expr,returnByValue=True,awaitPromise=True))
        if 'exceptionDetails' in r: raise RuntimeError(r['exceptionDetails'])
        return r.get('result',{}).get('value')

    def open(self,path):
        self.call('Page.navigate',dict(url=path.resolve().as_uri()))
        deadline=time.monotonic()+10
        while not self.js('Boolean(window.ER03C?.state.ready)'):
            if time.monotonic()>deadline: raise TimeoutError('Chart did not initialize: '+str(path))
            time.sleep(.05)
        self.js('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')

    def click(self,selector):
        rect=self.js(f'(()=>{{const r=document.querySelector({json.dumps(selector)}).getBoundingClientRect();return {{x:r.x+r.width/2,y:r.y+r.height/2}};}})()')
        for kind in ('mouseMoved','mousePressed','mouseReleased'):
            self.call('Input.dispatchMouseEvent',dict(type=kind,**rect,button='left',clickCount=1))

    def shot(self,name):
        self.js('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
        b=self.call('Page.captureScreenshot',dict(format='png',captureBeyondViewport=True))['data']
        (OUT/name).write_bytes(base64.b64decode(b))

    def __exit__(self,*args):
        self.ws.close();self.proc.terminate()
        try:self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:self.proc.kill();self.proc.wait()
        self.profile.cleanup()


def reference_tests(c):
    checks=[]
    def check(name,expr):
        if not c.js(expr): raise AssertionError(name)
        checks.append(name)
    c.open(OUT/'TIPSMUSIC.html')
    check('392 actual candlesticks','ER03C.series.price.data().length===392 && ER03C.series.price.seriesType()==="Candlestick"')
    check('all candle OHLC match exported data','ER03C.series.price.data().every((p,i)=>["open","high","low","close"].every(k=>p[k]===ER03C.data.rows[i][k]))')
    check('daily aligned series preserve missing dates','Object.values(ER03C.series).every(s=>s.data().every(p=>ER03C.data.rows.some(r=>r.date===p.time))) && ER03C.series.rs55.data().length===172 && ER03C.series.dss_pct.data().length===372')
    check('purple DSS','ER03C.series.dss_pct.options().color==="#b47bea"')
    check('RS zero baseline registered','ER03C.zeroLine.options().price===0 && ER03C.zeroLine.options().axisLabelVisible')
    check('classified volume bars use original reconstructed color','ER03C.series.volume.data().every(p=>{const r=ER03C.data.rows.find(r=>r.date===p.time);return p.color===r.simple.original_final_color && p.value===r.volume}) && ER03C.series.volume.data().length===ER03C.data.rows.filter(r=>r.simple.original_final_color!==null).length')
    c.shot('TIPSMUSIC-full-history.png')
    # Real pointer moves in each panel trigger subscription and synchronized
    # selected-candle updates, rather than calling our selection API directly.
    for panel in ('price','dss','volume','rs'):
        rect=c.js(f'(()=>{{const r=document.getElementById("{panel}").getBoundingClientRect();return {{x:r.x+r.width*.55,y:r.y+70}}}})()')
        c.call('Input.dispatchMouseEvent',dict(type='mouseMoved',**rect))
        check('pointer synchronized from '+panel,'Object.values(ER03C.state.crosshairs).every(t=>t===ER03C.data.rows[ER03C.state.selected].date) && document.getElementById("inspection").textContent.includes(ER03C.data.rows[ER03C.state.selected].date)')
    for key in ('ema10','ema20','dtr','dss','volume','rs','profile'):
        before=c.js(f'ER03C.state.visible.{key}')
        c.click('#toggle-'+key)
        check('independent toggle '+key,f'ER03C.state.visible.{key}==={json.dumps(not before)}')
        if key in ('ema10','ema20'):check('series visibility '+key,f'ER03C.series.{key}.options().visible==={json.dumps(not before)}')
        elif key=='dtr':check('DTR box visibility','document.getElementById("dtr-box").hidden')
        elif key!='profile':check('panel visibility '+key,f'document.getElementById("{key}-panel").hidden')
        c.click('#toggle-'+key)
    check('DTR box is above chart canvas','(()=>{const d=document.getElementById("dtr-box");return Number(getComputedStyle(d).zIndex)>2 && !d.hidden})()')
    # Keyboard inspection checks a selected historical date and its exact text.
    c.js('ER03C.select(ER03C.data.rows.findIndex(r=>r.date==="2026-08-18"))')
    check('dated OHLC tooltip','document.getElementById("inspection").textContent.includes("655.70") && document.getElementById("inspection").textContent.includes("2026-08-18")')
    c.call('Input.dispatchKeyEvent',dict(type='keyDown',key='ArrowLeft',code='ArrowLeft',windowsVirtualKeyCode=37))
    check('previous candle keyboard','ER03C.data.rows[ER03C.state.selected].date==="2026-08-17"')
    c.click('#next')
    check('next candle button','ER03C.data.rows[ER03C.state.selected].date==="2026-08-18"')
    c.js('ER03C.select(0)')
    check('missing values never rendered as zero','ER03C.data.rows[0].rs55===null && !ER03C.series.rs55.data().some(p=>p.time===ER03C.data.rows[0].date) && document.getElementById("dss-value").textContent.includes("UNAVAILABLE")')
    c.click('#recent')
    check('zoom range synchronized','(()=>{const a=Object.values(ER03C.charts).map(c=>c.timeScale().getVisibleLogicalRange());return a.every(r=>Math.abs(r.from-a[0].from)<1e-6&&Math.abs(r.to-a[0].to)<1e-6)})()')
    rect=c.js('(()=>{const r=document.getElementById("price").getBoundingClientRect();return {x:r.x+500,y:r.y+180}})()')
    before=c.js('ER03C.charts.price.timeScale().getVisibleLogicalRange()')
    c.call('Input.dispatchMouseEvent',dict(type='mouseWheel',**rect,deltaX=0,deltaY=-180))
    c.js('new Promise(r=>setTimeout(r,250))')
    after=c.js('ER03C.charts.price.timeScale().getVisibleLogicalRange()')
    if abs((after['to']-after['from'])-(before['to']-before['from']))<1:raise AssertionError('Wheel zoom failed')
    checks.append('real wheel zoom')
    before=after
    c.call('Input.dispatchMouseEvent',dict(type='mousePressed',**rect,button='left',clickCount=1))
    for delta in (30,70,120):c.call('Input.dispatchMouseEvent',dict(type='mouseMoved',x=rect['x']+delta,y=rect['y'],button='left',buttons=1))
    c.call('Input.dispatchMouseEvent',dict(type='mouseReleased',x=rect['x']+120,y=rect['y'],button='left',clickCount=1))
    after=c.js('ER03C.charts.price.timeScale().getVisibleLogicalRange()')
    if abs(after['from']-before['from'])<1:raise AssertionError('Drag pan failed')
    checks.append('real drag pan')
    check('zoom/pan remains synchronized','(()=>{const a=Object.values(ER03C.charts).map(c=>c.timeScale().getVisibleLogicalRange());return a.every(r=>Math.abs(r.from-a[0].from)<1e-6&&Math.abs(r.to-a[0].to)<1e-6)})()')
    c.js('document.getElementById("window").value="1";document.getElementById("window").dispatchEvent(new Event("change"))')
    check('approximate window boundaries','document.getElementById("windows-overlay").innerHTML.includes("APPROXIMATE")')
    c.js('ER03C.select(ER03C.data.rows.findIndex(r=>r.date==="2026-08-18"))')
    c.click('#toggle-profile');c.shot('TIPSMUSIC-inspection.png')
    check('71 available profiles; no daily manufactured levels','ER03C.data.rows.filter(r=>r.profile!==null).length===71 && ER03C.data.rows[0].profile===null')
    if c.errors or c.remote:raise AssertionError(dict(errors=c.errors,remote=c.remote))
    return checks


def run(action):
    OUT.mkdir(exist_ok=True)
    with Chrome() as c:
        if action=='reference':
            checks=reference_tests(c)
            write_json(OUT/'reference-browser-validation.json',dict(passed=True,checks=checks,
                html_sha256=digest(OUT/'TIPSMUSIC.html'),
                asset_hashes={p.name:digest(p) for p in (OUT/'assets').iterdir() if p.is_file()},
                js_errors=c.errors,remote_requests=c.remote,mode='Chrome headless, isolated profile, file://, network blocked'))
            print(f'{len(checks)} reference browser checks passed')
        else:
            names=json.loads((OUT/'pilot-chart-index.json').read_text())['charts']
            summaries=[]
            for name in names:
                c.open(OUT/name)
                result=c.js('({symbol:ER03C.data.symbol,bars:ER03C.series.price.data().length,ready:ER03C.state.ready,panels:Object.keys(ER03C.charts).length,rsAvailable:ER03C.data.rows.filter(r=>r.rs55!==null).length})')
                if not result['ready'] or not result['bars'] or result['panels']!=4:raise AssertionError(name)
                summaries.append(dict(file=name,**result))
            if c.errors or c.remote:raise AssertionError(dict(errors=c.errors,remote=c.remote))
            files=[OUT/n for n in names]+list((OUT/'assets').iterdir())
            write_json(OUT/'all-browser-validation.json',dict(passed=True,charts=summaries,
                js_errors=c.errors,remote_requests=c.remote,
                verified_hashes={str(p.relative_to(OUT)):digest(p) for p in files if p.is_file()}))
            print(f'{len(names)} packaged charts rendered offline without JS errors')


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('action',choices=['reference','all']);run(p.parse_args().action)
