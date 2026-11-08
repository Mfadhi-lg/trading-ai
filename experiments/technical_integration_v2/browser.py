"""Browser-check TECH-06 using the existing local Chrome/CDP harness."""
import json
import base64
from .build import OUT
from experiments.earnings_reaction_v1.er03_chart_browser import Chrome

def main():
    checks=[]
    with Chrome() as c:
        def check(name,expr):
            value=c.js(expr)
            if not value: raise AssertionError(name+': '+str(value)+' errors '+str(c.errors))
            checks.append(name)
        c.call('Page.navigate',dict(url=(OUT/'charts/sector-rotation.html').resolve().as_uri()))
        import time
        deadline=time.monotonic()+15
        while not c.js('Boolean(window.TECH06?.data)'):
            if time.monotonic()>deadline: raise TimeoutError(str(c.errors))
            time.sleep(.1)
        check('139 catalog identities rendered','TECH06.data.inventory.length===139&&document.querySelectorAll("#inventory tr").length===140')
        check('Four price histories and no VIX return ranking','new Set(TECH06.data.rows.map(r=>r.instrument_key)).size===4&&!TECH06.data.rows.some(r=>r.official_name==="India VIX")')
        check('Price comparison and RS55 charts initialized','TECH06.charts.price!==TECH06.charts.rs')
        c.js('TECH06.inspect("2026-03-10")')
        check('Selected date synchronized with context','TECH06.selectedDate==="2026-03-10"&&document.getElementById("readout").textContent.startsWith("2026-03-10")&&document.querySelectorAll("#context tr").length===31')
        check('Context exposes all categories and unavailable futures','document.getElementById("context").textContent.includes("GIFT_NIFTY")&&document.getElementById("context").textContent.includes("UNAVAILABLE")')
        c.js('document.getElementById("filter").value="thematic";document.getElementById("filter").dispatchEvent(new Event("input"))')
        check('Thematic filter includes unqueried catalog histories','document.querySelectorAll("#inventory tr").length===42&&document.getElementById("inventory").textContent.includes("NOT_QUERIED")')
        c.js('document.getElementById("filter").value="";document.getElementById("filter").dispatchEvent(new Event("input"));TECH06.inspect("2026-10-30")')
        c.js('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
        (OUT/'charts/sector-rotation.png').write_bytes(base64.b64decode(c.call('Page.captureScreenshot',dict(format='png',captureBeyondViewport=False))['data']))
        for r in json.loads((OUT/'chart-preservation.json').read_text()):
            c.open(OUT/'charts'/r['chart'])
            check('Original chart ready '+r['chart'],'ER03C.state.ready===true')
            c.js('ER03C.select(ER03C.data.rows.findIndex(r=>r.date==="2026-08-11"))')
            check('Updated context category gate '+r['chart'],'document.getElementById("market-context").textContent.includes("Strict verified delivery features: unavailable")&&document.getElementById("market-context").dataset.selectedDate==="2026-08-11"')
            check('NIFTYMIDSML400 comparison populated '+r['chart'],'ER03C.series["tech.normalized_midsml400"].data().filter(r=>r.value!==undefined).length===227')
            if r['chart'].startswith('stocks/'):
                check('Original stock technical panels intact '+r['chart'],'["ema10","ema20","dss_pct","volume","rs55","tech.loo_rs55"].every(k=>ER03C.series[k])')
        check('No JavaScript exceptions',not c.errors and 'true' or 'false')
        check('No remote chart requests',not c.remote and 'true' or 'false')
        data=dict(passed=True,assertions=len(checks),checks=checks,javascript_errors=c.errors,remote_requests=c.remote,
                  browser='Installed local Chrome headless; existing TECH-05 CDP harness',charts_tested=16)
        (OUT/'browser-validation.json').write_text(json.dumps(data,indent=2)+'\n')
        print(json.dumps(dict(passed=True,assertions=len(checks),charts=16)))

if __name__=='__main__':main()
