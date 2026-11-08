"""Independent numerical audit and actual offline ER03C-renderer checks."""
from decimal import Decimal as D
from fractions import Fraction as F
import json
from .er03 import BENCH, write_csv, write_json, read_csv, digest, ROOT
from .er04 import OUT, COMPLETION, load_data, preservation, retained_profiles
from . import er03_chart_upgrade as chart

def numerical():
    data,_=load_data();bm={str(c.start_ist.date()):c for c in data[BENCH]};checks=[]
    def check(symbol,day,field,actual,expected):
        if isinstance(expected,F): expected=D(expected.numerator)/D(expected.denominator)
        if actual is None or expected is None or isinstance(expected,(bool,str)):
            passed=actual==expected;delta=None
        else:delta=D(actual)-D(expected);passed=abs(delta)<=D('1e-18')
        checks.append(dict(symbol=symbol,date=day,field=field,actual=actual,expected=expected,signed_difference=delta,passed=passed))
    for p in read_csv(COMPLETION.parent/'pilot-companies.csv'):
        symbol=p['symbol'];bars=data[p['instrument_key']]
        payload=chart.assemble(symbol,p['instrument_key'],bars,bm)
        ema={10:None,20:None};trs=[];atr=None
        closes=[F(c.close) for c in bars];vols=[F(c.volume) for c in bars]
        for i,(r,c) in enumerate(zip(payload['rows'],bars)):
            for n in ema:
                a=F(2,n+1);ema[n]=closes[i] if i==0 else a*closes[i]+(1-a)*ema[n]
                check(symbol,r['date'],f'ema{n}',r[f'ema{n}'],ema[n])
            if i:
                tr=max(F(c.high-c.low),abs(F(c.high)-closes[i-1]),abs(F(c.low)-closes[i-1]));trs.append(tr)
                if len(trs)==14:atr=sum(trs)/14
                elif len(trs)>14:atr=(atr*13+tr)/14
            check(symbol,r['date'],'atr14',r['atr14'],atr)
            check(symbol,r['date'],'daily_range',r['daily_range'],c.high-c.low)
            check(symbol,r['date'],'dss_pct',r['dss_pct'],100*vols[i]*20/sum(vols[i-20:i]) if i>=20 and sum(vols[i-20:i]) else None)
            seq=bars[i-55:i+1] if i>=55 else []
            rs=None
            if len(seq)==56 and all(str(c.start_ist.date()) in bm for c in seq):
                rs=closes[i]/closes[i-55]/(F(bm[r['date']].close)/F(bm[str(seq[0].start_ist.date())].close))-1
            check(symbol,r['date'],'rs55',r['rs55'],rs)
            sv=r['simple']
            check(symbol,r['date'],'simple.volume_sma',sv['volume_sma'],sum(vols[i-20:i+1])/21 if i>=20 else None)
            pp=None
            if i>=10:
                down=[vols[j] for j in range(i-10,i) if bars[j].close<bars[j].open]
                pp=c.close>=c.open and vols[i]>max(down,default=F(0))
            check(symbol,r['date'],'simple.pocket_pivot',sv['pocket_pivot'],pp)
            check(symbol,r['date'],'simple.color_ready',sv['original_final_color'] is not None,i>=251)
    write_csv(COMPLETION/'numerical-comparisons.csv',checks)
    summary=dict(checks=len(checks),failures=sum(not r['passed'] for r in checks),methods='Independent Fraction EMA/RMA/DSS/RS/SMA and direct PP/252-bar readiness; pure Upstox inputs')
    write_json(COMPLETION/'numerical-verification.json',summary)
    if summary['failures']:raise AssertionError(summary)
    # Rerun retained ER03C full reference audit without overwriting earlier evidence.
    original=chart.OUT;chart.OUT=COMPLETION/'retained-reference-audit';chart.OUT.mkdir(exist_ok=True)
    try:
        # Recreate the exact disclosed mixed-feed reference only for this audit.
        from .compare_ta03r4 import TIPSMUSIC
        module=chart.volume_module();_,tv=module.reference_candles('daily-listing-history',1440)
        bars=data[TIPSMUSIC];prefix=tuple(c for c in tv if c.start_ist<bars[0].start_ist)
        # Profile evidence does not enter this numerical audit.
        profiles={r['date']:r['profile'] for r in json.loads((original/'TIPSMUSIC-data.json').read_text())['rows'] if r['profile'] is not None}
        payload=chart.assemble('TIPSMUSIC',TIPSMUSIC,bars,bm,prefix,profiles)
        summary['retained_reference']=chart.compare_checks(payload,bm)
    finally:chart.OUT=original
    write_json(COMPLETION/'numerical-verification.json',summary)
    preservation();print(json.dumps(summary))

def browser():
    from . import er03_chart_browser as old
    dest=OUT/'charts';old.OUT=dest
    summaries=[];interaction=[]
    with old.Chrome() as c:
        for name in json.loads((OUT/'chart-index.json').read_text())['charts']:
            c.open(dest/name)
            result=c.js('({ready:ER03C.state.ready,bars:ER03C.series.price.data().length,panels:Object.keys(ER03C.charts).length,exactOHLC:ER03C.series.price.data().every((p,i)=>["open","high","low","close"].every(k=>p[k]===ER03C.data.rows[i][k])),exactVolume:ER03C.series.volume.data().every(p=>{const r=ER03C.data.rows.find(r=>r.date===p.time);return p.color===r.simple.original_final_color&&p.value===r.volume})})')
            if not result['ready'] or not result['bars'] or result['panels']!=4 or not result['exactOHLC'] or not result['exactVolume']:raise AssertionError(name)
            summaries.append(dict(file=name,**result))
        for symbol in ('TIPSMUSIC','TARIL'):
            name=next(n for n in json.loads((OUT/'chart-index.json').read_text())['charts'] if symbol+'-S3' in n)
            c.open(dest/name)
            def check(label,expr):
                if not c.js(expr):raise AssertionError(label)
                interaction.append(symbol+':'+label)
            for panel in ('price','dss','volume','rs'):
                rect=c.js(f'(()=>{{const r=document.getElementById("{panel}").getBoundingClientRect();return {{x:r.x+r.width*.55,y:r.y+70}}}})()')
                c.call('Input.dispatchMouseEvent',dict(type='mouseMoved',**rect))
                check('pointer '+panel,'Object.values(ER03C.state.crosshairs).every(t=>t===ER03C.data.rows[ER03C.state.selected].date)')
            for key in ('ema10','ema20','dtr','dss','volume','rs','profile'):
                if c.js(f'document.getElementById("toggle-{key}").disabled'):
                    check('unsupported profile disabled',f'{json.dumps(key)}==="profile" && ER03C.data.statuses.profile==="UNAVAILABLE"')
                    continue
                before=c.js(f'ER03C.state.visible.{key}');c.click('#toggle-'+key)
                check('toggle '+key,f'ER03C.state.visible.{key}==={json.dumps(not before)}');c.click('#toggle-'+key)
            c.js('ER03C.select(ER03C.data.rows.findIndex(r=>r.date===ER03C.data.initial.last))')
            c.js('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
            selected=c.js('ER03C.state.selected');c.click('#next')
            check('next tooltip',f'ER03C.state.selected==={selected+1} && document.getElementById("inspection").textContent.includes(ER03C.data.rows[{selected+1}].date)')
            c.call('Input.dispatchKeyEvent',dict(type='keyDown',key='ArrowLeft',code='ArrowLeft',windowsVirtualKeyCode=37))
            check('keyboard previous',f'ER03C.state.selected==={selected}')
            c.click('#recent')
            check('shared zoom','(()=>{const a=Object.values(ER03C.charts).map(c=>c.timeScale().getVisibleLogicalRange());return a.every(r=>Math.abs(r.from-a[0].from)<1e-6&&Math.abs(r.to-a[0].to)<1e-6)})()')
            rect=c.js('(()=>{const r=document.getElementById("price").getBoundingClientRect();return {x:r.x+500,y:r.y+180}})()')
            before=c.js('ER03C.charts.price.timeScale().getVisibleLogicalRange()')
            c.call('Input.dispatchMouseEvent',dict(type='mouseWheel',**rect,deltaX=0,deltaY=-180))
            c.js('new Promise(r=>setTimeout(r,250))')
            check('wheel zoom',f'Math.abs(ER03C.charts.price.timeScale().getVisibleLogicalRange().from-{before["from"]})>.01')
            for kind,x in (('mousePressed',rect['x']),('mouseMoved',rect['x']+90),('mouseReleased',rect['x']+90)):
                c.call('Input.dispatchMouseEvent',dict(type=kind,x=x,y=rect['y'],button='left',buttons=1 if kind!='mouseReleased' else 0,clickCount=1))
            check('pan range retained','ER03C.charts.price.timeScale().getVisibleLogicalRange().to>ER03C.charts.price.timeScale().getVisibleLogicalRange().from')
            c.shot(symbol+'-checkpoint.png')
        if c.errors or c.remote:raise AssertionError(dict(errors=c.errors,remote=c.remote))
        write_json(OUT/'browser-validation.json',dict(passed=True,charts=summaries,interaction_checks=interaction,js_errors=c.errors,remote_requests=c.remote,
            verified_hashes={str(p.relative_to(OUT)):digest(p) for p in dest.rglob('*') if p.is_file()}))
    print(len(summaries),'offline charts;',len(interaction),'interaction checks')

if __name__=='__main__':
    import sys
    browser() if sys.argv[1:]==['browser'] else numerical()
