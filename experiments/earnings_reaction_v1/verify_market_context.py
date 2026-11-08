"""Actual offline browser checks for existing ER03C charts with macro inspection."""
import json
from .er03 import write_json, digest
from .market_context import OUT

def main():
    from . import er03_chart_browser as browser
    browser.OUT=OUT/'charts'
    names=json.loads((OUT/'chart-index.json').read_text())['charts']
    checks=[]
    with browser.Chrome() as c:
        for name in names:
            c.open(OUT/'charts'/name)
            def check(label,expression):
                if not c.js(expression):raise AssertionError(name+':'+label)
                checks.append(dict(chart=name,check=label,passed=True))
            check('four existing panels','Object.keys(ER03C.charts).length===4')
            check('original OHLC unchanged','ER03C.series.price.data().every((p,i)=>["open","high","low","close"].every(k=>p[k]===ER03C.data.rows[i][k]))')
            check('real volume with validated colors only','ER03C.series.volume.data().every(p=>{const r=ER03C.data.rows.find(r=>r.date===p.time);return p.color===r.simple.original_final_color&&p.value===r.volume})')
            c.js('ER03C.select(ER03C.data.rows.findIndex(r=>r.date===ER03C.data.initial.last))')
            check('context initial date synchronized','document.getElementById("market-context").dataset.selectedDate===ER03C.data.rows[ER03C.state.selected].date')
            check('missing quotes explicit','document.getElementById("market-context").textContent.includes("NO_HISTORICAL_PREOPEN_QUOTES") && document.getElementById("market-context").textContent.includes("PUBLICATION_TIMESTAMP_UNAVAILABLE")')
            check('two phases, eleven series','document.querySelectorAll("#market-context table tr").length===23')
            check('currency release provenance visible','document.getElementById("market-context").textContent.includes("ACTUAL_RELEASE_MINUTE_UNAVAILABLE")')
            old=c.js('ER03C.state.selected');c.click('#next')
            check('next candle updates context',f'ER03C.state.selected==={old+1} && document.getElementById("market-context").dataset.selectedDate===ER03C.data.rows[{old+1}].date')
            rect=c.js('(()=>{const r=document.getElementById("price").getBoundingClientRect();return {x:r.x+r.width*.55,y:r.y+80}})()')
            c.call('Input.dispatchMouseEvent',dict(type='mouseMoved',**rect))
            check('pointer updates context and four crosshairs','Object.values(ER03C.state.crosshairs).every(t=>t===ER03C.data.rows[ER03C.state.selected].date)&&document.getElementById("market-context").dataset.selectedDate===ER03C.data.rows[ER03C.state.selected].date')
            c.js('ER03C.select(ER03C.data.rows.findIndex(r=>r.date===ER03C.data.initial.last))')
            if 'AETHER' in name or 'SHILCTECH' in name:c.shot(name.replace('.html','.png'))
        if c.errors or c.remote:raise AssertionError(dict(js_errors=c.errors,remote_requests=c.remote))
        write_json(OUT/'browser-validation.json',dict(passed=True,checks=checks,js_errors=c.errors,remote_requests=c.remote,
            chart_hashes={str(p.relative_to(OUT)):digest(p) for p in (OUT/'charts').rglob('*') if p.is_file()}))
    print(len(checks),'browser assertions; no remote requests or JS errors')

if __name__=='__main__':main()
