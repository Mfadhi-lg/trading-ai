"""Offline ER03C closing-index/peer adapter checks in actual Chrome."""
import json
from .sources import OUT
from experiments.earnings_reaction_v1.er03 import write_json,digest

def main():
    from experiments.earnings_reaction_v1 import er03_chart_browser as old
    old.OUT=OUT/'charts';checks=[]
    names=json.loads((OUT/'chart-index.json').read_text())['charts']
    with old.Chrome() as c:
        for name in names:
            c.open(OUT/'charts'/name)
            def check(label,expr):
                if not c.js(expr):raise AssertionError(name+': '+label)
                checks.append(dict(chart=name,assertion=label,passed=True))
            is_index=name.endswith('-index.html')
            check('four original panel containers','Object.keys(ER03C.charts).length===4')
            if is_index:
                check('closing LineSeries, no candlesticks','ER03C.series.price.seriesType()==="Line"')
                check('actual computed closes match every rendered value','ER03C.series.price.data().every((p,i)=>p.time===ER03C.data.rows[i].date&&p.value===ER03C.data.rows[i].index_close)')
                check('no fabricated index OHLC or aggregate share-volume fields','ER03C.data.rows.every(r=>!["open","high","low","volume"].some(k=>k in r))')
                check('breadth panel uses percent LineSeries','ER03C.series.volume.seriesType()==="Line"&&ER03C.series.pct_rvol_gt1.data().filter(p=>p.value!==undefined).every(p=>p.value>=0&&p.value<=100)')
                check('base value exactly 1000','ER03C.series.price.data()[0].value===1000')
                check('unsupported DTR and profile controls absent','!document.getElementById("toggle-dtr")&&!document.getElementById("toggle-profile")')
            else:
                check('actual stock candlesticks retained','ER03C.series.price.seriesType()==="Candlestick"&&ER03C.series.price.data().every((p,i)=>["open","high","low","close"].every(k=>p[k]===ER03C.data.rows[i][k]))')
                check('target excluded from two peer names','ER03C.data.peer_members.length===2&&!ER03C.data.peer_members.includes(ER03C.data.symbol)')
                check('peer and full-subset RS lines available','ER03C.series.peer_rs55.data().some(p=>p.value!==undefined)&&ER03C.series.subsector_rs55.data().some(p=>p.value!==undefined)')
            old_index=c.js('ER03C.state.selected')
            c.click('#prev')
            check('candle navigation and peer inspection synchronized',f'ER03C.state.selected==={old_index-1}&&document.getElementById("peer-inspection").dataset.selectedDate===ER03C.data.rows[ER03C.state.selected].date')
            rect=c.js('(()=>{const r=document.getElementById("price").getBoundingClientRect();return {x:r.x+r.width*.6,y:r.y+80}})()')
            c.call('Input.dispatchMouseEvent',dict(type='mouseMoved',**rect))
            check('pointer synchronizes all panels and context','Object.values(ER03C.state.crosshairs).every(t=>t===ER03C.data.rows[ER03C.state.selected].date)&&document.getElementById("peer-inspection").dataset.selectedDate===ER03C.data.rows[ER03C.state.selected].date')
            for key in ('ema10','ema20','dss','volume','rs'):
                before=c.js(f'ER03C.state.visible.{key}');c.click('#toggle-'+key)
                check('retained toggle '+key,f'ER03C.state.visible.{key}==={json.dumps(not before)}')
                c.click('#toggle-'+key)
            if name in ('FY2027-Q1-EMS-index.html','FY2027-Q1-TYRES-index.html','FY2027-Q1-TRANSFORMERS-index.html','FY2027-Q1-PGEL-peers.html'):
                if is_index:c.js('ER03C.select(ER03C.data.rows.findIndex(r=>r.date==="2026-08-11"))')
                else:c.js('ER03C.select(ER03C.data.rows.findIndex(r=>r.date===ER03C.data.initial.last))')
                c.shot(name.replace('.html','.png'))
        if c.errors or c.remote:raise AssertionError(dict(errors=c.errors,remote=c.remote))
        write_json(OUT/'browser-validation.json',dict(passed=True,charts=len(names),assertions=len(checks),checks=checks,js_errors=c.errors,remote_requests=c.remote,
            hashes={str(p.relative_to(OUT)):digest(p) for p in (OUT/'charts').rglob('*') if p.is_file()}))
    print(len(names),'offline charts;',len(checks),'browser assertions; no JS errors or remote requests')

if __name__=='__main__':main()
