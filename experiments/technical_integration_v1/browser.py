"""Actual offline Chrome integration checks using the existing ER03C harness."""
import json
from .prepare import OUT
from experiments.earnings_reaction_v1.er03 import write_json

def main():
    from experiments.earnings_reaction_v1 import er03_chart_browser as old
    old.OUT=OUT/'charts';checks=[]
    with old.Chrome() as c:
        c.call('Emulation.setDeviceMetricsOverride',dict(width=1600,height=2200,deviceScaleFactor=1,mobile=False))
        for name in json.loads((OUT/'chart-index.json').read_text())['charts']:
            c.open(OUT/'charts'/name);stock=name.startswith('stocks/')
            def check(label,expr):
                if not c.js(expr):raise AssertionError(dict(chart=name,assertion=label,errors=c.errors,ma_diagnostics=c.js('ER03C.series["tech.sma20"].data().map((r,i)=>({point:r,expected:ER03C.data.rows[i].tech?.sma20})).filter(x=>x.point.value!==x.expected&&!(x.point.value===undefined&&x.expected==null)).slice(0,3)')))
                checks.append(dict(chart=name,assertion=label,passed=True))
            check('existing panels plus separate activity','Object.keys(ER03C.charts).length==='+str(7 if stock else 5))
            check('histogram not raw share-volume aggregation','ER03C.series.tech_activity.seriesType()==="Histogram"')
            # The retained library may omit leading whitespace in data().
            # Match observations by date instead of shifting warmup indices.
            check('every histogram value matches retained proxy','ER03C.series.tech_activity.data().every(r=>{const t=ER03C.data.rows.find(x=>x.date===r.time)?.tech;return r.value===t?.value||(r.value===undefined&&t?.value==null)})')
            check('colors encode same-day index direction','ER03C.series.tech_activity.data().every(r=>r.value===undefined||r.color===({GREEN:"#26a69a",RED:"#ef5350",NEUTRAL:"#8996a6"})[ER03C.data.rows.find(x=>x.date===r.time).tech.color])')
            check('MA20 uses calculated warmup without artificial zero','ER03C.series["tech.sma20"].data().every(r=>{const t=ER03C.data.rows.find(x=>x.date===r.time)?.tech;return r.value===t?.sma20||(r.value===undefined&&t?.sma20==null)})&&ER03C.series["tech.sma20"].data().filter(r=>r.value!==undefined).length===ER03C.data.rows.filter(r=>r.tech?.sma20!=null).length')
            check('actual subgroup data exists','ER03C.series.tech_activity.data().filter(r=>r.value!==undefined).length===227')
            if stock:
                check('stock candlesticks and indicators retained','ER03C.series.price.seriesType()==="Candlestick"&&ER03C.series.price.data().every((r,i)=>["open","high","low","close"].every(k=>r[k]===ER03C.data.rows[i][k]))')
                check('DTR/DSS/SimpleVolume/RS still present','["ema10","ema20","dss_pct","volume","rs55"].every(k=>ER03C.series[k])&&ER03C.data.rows.some(r=>r.dtr_atr_pct!==null&&r.simple!==undefined)')
                check('self excluded from independent core peers','ER03C.data.rows.filter(r=>r.tech?.loo_members).every(r=>!r.tech.loo_members.split(";").includes(ER03C.data.symbol))')
                check('independent peer RS rendered','ER03C.series["tech.loo_rs55"].data().some(r=>r.value!==undefined)')
                check('subsector breadth is separate from stock volume','ER03C.charts.participation!==ER03C.charts.volume&&ER03C.series["tech.pct_above"].data().some(r=>r.value!==undefined)')
            else:
                check('closing index line retained','ER03C.series.price.seriesType()==="Line"')
                check('breadth remains a separate panel','ER03C.charts.volume!==ER03C.charts.activity&&ER03C.series.pct_above_ema20.seriesType()==="Line"')
                check('no synthetic OHLC/index share volume','ER03C.data.rows.every(r=>!["open","high","low","volume"].some(k=>k in r))')
            c.js('ER03C.range("2026-07-01","2026-09-30")')
            c.js('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
            c.js('ER03C.select(ER03C.data.rows.findIndex(r=>r.date==="2026-08-11"))')
            check('selected activity and context inspection synchronized','document.getElementById("activity-value").dataset.selectedDate==="2026-08-11"&&document.getElementById("market-context").dataset.selectedDate==="2026-08-11"')
            check('group ratio/member RVOL distinguished','document.getElementById("activity-value").textContent.includes("Relative GROUP")&&document.getElementById("activity-value").textContent.includes("Median MEMBER")')
            check('global context and missing futures explicit','document.getElementById("market-context").textContent.includes("SP500")&&document.getElementById("market-context").textContent.includes("Futures snapshots are unavailable")')
            key='tech.normalized_official'
            expected=not ('TRANSFORMERS' in name or 'VOLTAMP' in name)
            check('official sector populated or explicitly unavailable','ER03C.series["'+key+'"].data().some(r=>r.value!==undefined)==='+json.dumps(expected))
            rect=c.js('(()=>{const r=document.getElementById("prev").getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()')
            c.call('Input.dispatchMouseEvent',dict(type='mouseMoved',**rect));c.js('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
            c.js('ER03C.select(ER03C.data.rows.findIndex(r=>r.date==="2026-08-11"))');selected=c.js('ER03C.state.selected')
            for kind in ('mousePressed','mouseReleased'):c.call('Input.dispatchMouseEvent',dict(type=kind,**rect,button='left',clickCount=1))
            check('navigation includes added panels',f'ER03C.state.selected==={selected-1}&&document.getElementById("activity-value").dataset.selectedDate===ER03C.data.rows[ER03C.state.selected].date')
            rect=c.js('(()=>{const r=document.getElementById("activity").getBoundingClientRect();return {x:r.x+r.width*.6,y:r.y+80}})()')
            c.call('Input.dispatchMouseEvent',dict(type='mouseMoved',**rect))
            check('activity crosshair synchronizes all panels','Object.values(ER03C.state.crosshairs).every(d=>d===ER03C.data.rows[ER03C.state.selected].date)&&Object.keys(ER03C.state.crosshairs).length===Object.keys(ER03C.charts).length')
            for key in ('ema10','ema20','dss','volume','rs','activity')+ (('comparison','participation') if stock else ()):
                before=c.js('ER03C.state.visible.'+key);c.click('#toggle-'+key)
                check('retained toggle '+key,'ER03C.state.visible.'+key+'==='+json.dumps(not before));c.click('#toggle-'+key)
            if name in ('indices/FY2027-Q1-TYRES-index.html','indices/FY2027-Q1-EMS-index.html','indices/FY2027-Q1-TRANSFORMERS-index.html','stocks/FY2027-Q1-PGEL-S3.html'):
                c.shot(name.replace('/','-').replace('.html','.png'))
        if c.errors or c.remote:raise AssertionError(dict(errors=c.errors,remote=c.remote))
        write_json(OUT/'browser-validation.json',dict(passed=True,charts=15,assertions=len(checks),js_errors=c.errors,remote_requests=c.remote,checks=checks))
    print(json.dumps(dict(charts=15,assertions=len(checks),passed=True)))

if __name__=='__main__':main()
