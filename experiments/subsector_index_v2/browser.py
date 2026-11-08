"""Actual local Chrome verification via retained ER03C browser harness."""
import json
from .prepare import OUT
from experiments.earnings_reaction_v1.er03 import write_json

def main():
    from experiments.earnings_reaction_v1 import er03_chart_browser as old
    old.OUT=OUT/'charts';checks=[]
    with old.Chrome() as c:
        for name in json.loads((OUT/'chart-index.json').read_text())['charts']:
            c.open(OUT/'charts'/name)
            def check(label,expr):
                if not c.js(expr):raise AssertionError(dict(chart=name,label=label,selected=c.js('ER03C.state.selected'),inspection=c.js('document.getElementById("peer-inspection").dataset.selectedDate'),errors=c.errors))
                checks.append(dict(chart=name,assertion=label,passed=True))
            check('four reused synchronized panels','Object.keys(ER03C.charts).length===4')
            check('actual closing-price line matches every value','ER03C.series.price.seriesType()==="Line"&&ER03C.series.price.data().every((r,i)=>r.value===ER03C.data.rows[i].index_close)')
            check('index rows contain no synthetic OHLC or share-volume sum','ER03C.data.rows.every(r=>!["open","high","low","volume"].some(k=>k in r))')
            check('comparison lines and sector EMAs populated','["normalized_subsector","normalized_sector_ema10","normalized_sector_ema20","normalized_nifty50","normalized_midsml400"].every(k=>ER03C.series[k].data().some(r=>r.value!==undefined))')
            check('missing Nifty50 dates stay missing','ER03C.data.rows.every(r=>r.nifty50_close!==null||r.normalized_nifty50===null)')
            check('breadth in percent','ER03C.series.pct_rvol_gt1.data().every(r=>r.value===undefined||(r.value>=0&&r.value<=100))')
            check('at least two independent peers','ER03C.data.peer_members.length>=2')
            if '-event.html' in name:
                symbol=name.split('-')[-2]
                check('stock excluded from peer set','!ER03C.data.peer_members.includes('+json.dumps(symbol)+')')
                check('earnings and five checkpoint markers','ER03C.data.events.length===6')
                check('subsequent outcome windows selectable','ER03C.data.windows.length===2')
                c.js('document.getElementById("window").value="0";document.getElementById("window").dispatchEvent(new Event("change"))')
                check('outcome period overlay rendered','document.getElementById("windows-overlay").innerHTML.includes("rect")')
            # A range change can enqueue pointer/crosshair updates. Place the
            # pointer over navigation and drain those before measuring a click.
            button=c.js('(()=>{const r=document.getElementById("prev").getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})()')
            c.call('Input.dispatchMouseEvent',dict(type='mouseMoved',**button))
            c.js('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
            c.js('ER03C.select(100)');selected=c.js('ER03C.state.selected')
            for kind in ('mousePressed','mouseReleased'):c.call('Input.dispatchMouseEvent',dict(type=kind,**button,button='left',clickCount=1))
            check('navigation synchronizes inspection',f'ER03C.state.selected==={selected-1}&&document.getElementById("peer-inspection").dataset.selectedDate===ER03C.data.rows[ER03C.state.selected].date')
            rect=c.js('(()=>{const r=document.getElementById("price").getBoundingClientRect();return {x:r.x+r.width*.6,y:r.y+80}})()')
            c.call('Input.dispatchMouseEvent',dict(type='mouseMoved',**rect))
            check('crosshair synchronizes dates','Object.values(ER03C.state.crosshairs).every(d=>d===ER03C.data.rows[ER03C.state.selected].date)')
            for key in ('ema10','ema20','dss','volume','rs'):
                before=c.js('ER03C.state.visible.'+key);c.click('#toggle-'+key)
                check('existing toggle '+key,'ER03C.state.visible.'+key+'==='+json.dumps(not before));c.click('#toggle-'+key)
            if name in ('FY2027-Q1-EMS-index.html','FY2027-Q1-PGEL-event.html','FY2027-Q1-VOLTAMP-event.html'):
                c.shot(name.replace('.html','.png'))
        if c.errors or c.remote:raise AssertionError(dict(errors=c.errors,remote=c.remote))
        write_json(OUT/'browser-validation.json',dict(charts=30,assertions=len(checks),passed=True,js_errors=c.errors,remote_requests=c.remote,checks=checks))
    print(json.dumps(dict(browser_charts=30,assertions=len(checks),passed=True)))

if __name__=='__main__':main()
