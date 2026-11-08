"""Extend the frozen TECH-05 chart copies; reuse its chart engine/library."""
import json
import shutil
from collections import defaultdict
from decimal import Decimal
from .build import OUT, OLD, ROOT, read, unpack

def packed(x):
    return json.dumps(x,default=lambda v:float(v) if isinstance(v,Decimal) else str(v),separators=(',',':')).replace('</','<\\/')

CONTEXT_JS = r'''
(()=>{const original=window.renderTechnical;
 const f=v=>v==null||v===''?'UNAVAILABLE':typeof v==='number'?v.toLocaleString('en-IN',{maximumFractionDigits:4}):String(v);
 window.renderTechnical=(day,t,c)=>{
  original(day,t,c);const panel=document.getElementById('market-context');panel.replaceChildren();
  const summary=document.createElement('summary');summary.textContent=day+' · TECH-06 · PREOPEN <09:15 / POSTCLOSE <18:00 Asia/Kolkata';panel.append(summary);
  const note=document.createElement('p');note.textContent='Strict verified delivery features: unavailable. H10 is a separate conservative dated reference, not live FX. Indian daily closes and oil are retrospective context. Upstox US bucket dates remain quarantined. Generic GIFT is not a contract-identified futures quote. Refresh intervals do not prove historical availability.';panel.append(note);
  const table=document.createElement('table');const header=document.createElement('tr');
  for(const x of ['Series / phase','Verified value','Conservative dated bound','Retrospective context','Date / age','Provenance and limitations']){let th=document.createElement('th');th.textContent=x;header.append(th)}table.append(header);
  for(const r of (window.TECH06_CONTEXT[day]||[])){
   const tr=document.createElement('tr');for(const x of [r.series+' / '+r.phase,f(r.verified_decision_time_value),f(r.conservative_time_bounded_value),f(r.retrospective_context_value)+' '+r.unit,r.observation_date+' / '+f(r.age_calendar_days)+' calendar days',r.category+'; '+r.stale_status+'; '+r.missing_data_reason]){let td=document.createElement('td');td.textContent=x;td.title=r.source_file+' | '+r.source_sha256+' | '+r.availability_basis+' | bound '+r.availability_upper_bound+' | '+r.observation_timestamp_basis;tr.append(td)}table.append(tr);
  }panel.append(table);panel.dataset.selectedDate=day;
 };
})();
'''

def render(rotation,snaps,indices):
    dest=OUT/'charts';shutil.copytree(OLD/'charts',dest,dirs_exist_ok=True)
    context=defaultdict(list)
    for rs in snaps.values():
        for r in rs: context[r['indian_session']].append(r)
    mids={r['date']:float(r['close']) for r in rotation if r['instrument_key']=='NSE_INDEX|NIFTY MIDSML 400' and r['close'] is not None}
    base=mids.get('2025-12-01')
    ledger=[]
    for kind in ('indices','stocks'):
        assets=dest/kind/'assets'
        (assets/'technical.js').write_text((OLD/'charts'/kind/'assets/technical.js').read_text()+'\n'+CONTEXT_JS)
        (assets/'technical-context.js').write_text((OLD/'charts'/kind/'assets/technical-context.js').read_text()+'\nwindow.TECH06_CONTEXT='+packed(context)+';\n')
        engine=(OLD/'charts'/kind/'assets/chart.js').read_text()
        anchor='  const profileSeries=[];'
        pane='comparison' if kind=='stocks' else 'dss'
        engine=engine.replace(anchor,"  line("+pane+",'tech.normalized_midsml400','#54bca8',{title:'NIFTYMIDSML400 · 1 Dec 2025=1000'});\n"+anchor)
        (assets/'chart.js').write_text(engine)
        for p in sorted((dest/kind).glob('*.html')):
            if p.name=='index.html': continue
            data,match=unpack(p.read_text());before=json.loads(json.dumps(data['rows']))
            for r in data['rows']:
                if r.get('tech') is not None: r['tech']['normalized_midsml400']=1000*mids[r['date']]/base if base and r['date'] in mids else None
            text=p.read_text();text=text[:match.start(2)]+packed(data)+text[match.end(2):]
            text=text.replace('../../TECHNICAL_ACCEPTANCE.md','../../TECHNICAL_BLOCK_STATUS.md')
            p.write_text(text)
            after,_=unpack(text)
            for a,b in zip(before,after['rows']):
                bt=b.copy()
                if bt.get('tech') is not None: bt['tech']=dict(bt['tech']);bt['tech'].pop('normalized_midsml400',None)
                if a!=bt: raise ValueError('Existing indicator field changed')
            ledger.append(dict(chart=str(p.relative_to(dest)),original_payload_rows_unchanged=True,rows=len(before)))
    (OUT/'chart-preservation.json').write_text(json.dumps(ledger,indent=2)+'\n')
    shutil.copy(OLD/'charts/indices/assets/lightweight-charts-5.0.9.js',dest/'lightweight-charts-5.0.9.js')
    dashboard(dest,rotation,indices,context)
    (dest/'index.html').write_text('<!doctype html><meta charset="utf-8"><h1>TECH-06 market context</h1><p><a href="sector-rotation.html">Sector rotation, full inventory and selected-date context</a></p>'+''.join('<p><a href="'+r['chart']+'">'+r['chart']+'</a></p>' for r in ledger))

def dashboard(dest,rows,indices,context):
    data=packed(dict(rows=rows,inventory=indices,context=context))
    text='''<!doctype html><html><head><meta charset="utf-8"><title>TECH-06 NSE sector rotation</title>
<style>body{background:#111922;color:#dfe7ef;font:14px system-ui;margin:24px}a{color:#9bc4ee}label{margin-right:15px}select,input,button{background:#202c38;color:inherit;padding:7px;border:1px solid #536171}table{border-collapse:collapse;width:100%;font-size:12px}td,th{padding:8px;text-align:left;border-bottom:1px solid #344150}th{cursor:pointer}#plot,#rsplot{height:300px}small{color:#acb6c0}.scroll{overflow:auto;max-height:420px}h1{font-size:24px}.measures{display:flex;gap:25px}section{margin-top:24px}#readout{margin:12px 0}</style></head><body>
<h1>NSE sector rotation · retained history</h1><p id="summary"></p>
<p>Descriptive measurements through 30 October 2026. Historical provider delivery is unverified. Rankings cover only retained comparable sectors; rising prices do not establish institutional accumulation. No index share-volume proxy is constructed.</p>
<label>Date <select id="day"></select></label><label>Compare <select id="compare"></select></label>
<div id="readout"></div><h2>Relative price performance · common 1 December 2025 base = 1,000</h2><div id="plot"></div><small>Date (Asia/Kolkata session) · Rebased index points. Source: retained Upstox daily closes; gaps are not filled. Selected official index, Nifty 50 and NIFTYMIDSML400.</small>
<h2>RS55 versus Nifty 50 · dimensionless ratio minus 1</h2><div id="rsplot"></div><small>Date · RS55, existing SS-INDEX-02 convention. Warmup requires 56 consecutive observations. Stock RS formulas remain unchanged.</small>
<section><h2>Available sector comparisons</h2><p id="leaders"></p><div class="scroll"><table id="ranking"></table></div></section>
<section><h2>Full Upstox NSE index inventory</h2><label>Filter <input id="filter" placeholder="Name or classification"></label><div class="scroll"><table id="inventory"></table></div></section>
<section><h2>Selected-date market context · strict cutoffs</h2><div class="scroll"><table id="context"></table></div></section>
<p><a href="indices/index.html">Custom subsector charts</a> · <a href="stocks/index.html">Stock comparisons</a> · <a href="../TECHNICAL_BLOCK_STATUS.md">Block status and limitations</a></p>
<script src="lightweight-charts-5.0.9.js"></script><script id="rotation-data" type="application/json">DATA</script><script>
const D=JSON.parse(document.getElementById('rotation-data').textContent),L=LightweightCharts;
const f=v=>v==null||v===''?'UNAVAILABLE':typeof v==='number'?v.toLocaleString('en-IN',{maximumFractionDigits:4}):String(v);
const opt={layout:{background:{color:'#111922'},textColor:'#dfe7ef'},grid:{vertLines:{color:'#24313d'},horzLines:{color:'#24313d'}},timeScale:{timeVisible:false}};
const price=L.createChart(document.getElementById('plot'),opt),rs=L.createChart(document.getElementById('rsplot'),opt);
let selectedSeries=[],rsSeries=null;const dates=[...new Set(D.rows.map(r=>r.date))].sort();
for(const date of dates){let o=new Option(date,date);day.add(o)}day.value=dates.at(-1);
const supported=D.inventory.filter(r=>r.daily_history_verified&&r.classification!=='VOLATILITY');for(const r of supported)compare.add(new Option(r.official_name,r.instrument_key));compare.value='NSE_INDEX|Nifty Auto';
summary.textContent=D.inventory.length+' catalog indices · '+D.inventory.filter(r=>r.daily_history_verified).length+' retained daily histories · '+supported.filter(r=>r.classification==='SECTORAL').length+' comparable sectors · '+D.inventory.filter(r=>r.intraday_history_verified).length+' retained intraday slice · 0 historical delivery-verified histories';
function table(id,columns,rows){const el=document.getElementById(id);el.replaceChildren();const head=document.createElement('tr');for(const [label] of columns){let t=document.createElement('th');t.textContent=label;head.append(t)}el.append(head);for(const r of rows){let tr=document.createElement('tr');for(const [,k] of columns){let td=document.createElement('td');td.textContent=f(r[k]);tr.append(td)}el.append(tr)}}
const cols=[['Index','official_name'],['1s %','return_1s_pct'],['5s %','return_5s_pct'],['10s %','return_10s_pct'],['20s %','return_20s_pct'],['40s %','return_40s_pct'],['EMA10','ema10'],['EMA20','ema20'],['RS55','rs55'],['RS direction','rs55_direction'],['Trend run','trend_persistence_sessions'],['20s rank','rank_20s'],['Rank improvement / 5s','rank_improvement_5s'],['Possible rotation','possible_rotation']];
function inspect(date){day.value=date;let sectors=D.rows.filter(r=>r.date===date&&r.classification==='SECTORAL').sort((a,b)=>(b.return_20s_pct??-Infinity)-(a.return_20s_pct??-Infinity));table('ranking',cols,sectors);const valid=sectors.filter(r=>r.return_20s_pct!=null);leaders.textContent=valid.length?'Strongest available: '+valid[0].official_name+'; weakest available: '+valid.at(-1).official_name+'. Improving: '+sectors.filter(r=>r.momentum_state==='IMPROVING').map(r=>r.official_name).join(', ')+'. Deteriorating: '+sectors.filter(r=>r.momentum_state==='DETERIORATING').map(r=>r.official_name).join(', ')+'. Leadership: '+sectors.filter(r=>r.leadership_state==='EMERGING_LEADERSHIP').map(r=>r.official_name).join(', '):'Return warmup on this date';
let r=D.rows.find(r=>r.date===date&&r.instrument_key===compare.value);readout.textContent=date+' | '+(r?r.official_name+' | Close '+f(r.close)+' points | Relative to Nifty50 '+f(r.relative_strength_vs_nifty50)+' | '+r.category:'No retained observation');
table('context',[['Series','series'],['Phase','phase'],['Category','category'],['Verified','verified_decision_time_value'],['Conservative','conservative_time_bounded_value'],['Retrospective','retrospective_context_value'],['Observed','observation_date'],['Availability bound','availability_upper_bound'],['Reason','missing_data_reason']],D.context[date]||[]);window.TECH06.selectedDate=date;}
function plot(){for(const s of selectedSeries)price.removeSeries(s);selectedSeries=[];if(rsSeries)rs.removeSeries(rsSeries);
const keys=[...new Set([compare.value,'NSE_INDEX|Nifty 50','NSE_INDEX|NIFTY MIDSML 400'])],colors=['#d7cba8','#9bc4ee','#54bca8'];
keys.forEach((key,i)=>{let rows=D.rows.filter(r=>r.instrument_key===key),base=rows.find(r=>r.date==='2025-12-01')?.close;let name=supported.find(r=>r.instrument_key===key)?.official_name;let s=price.addSeries(L.LineSeries,{color:colors[i],title:name});s.setData(rows.map(r=>base&&r.close!=null?{time:r.date,value:1000*r.close/base}:{time:r.date}));selectedSeries.push(s)});
rsSeries=rs.addSeries(L.LineSeries,{color:'#d7cba8',title:'RS55 / Nifty50'});rsSeries.setData(D.rows.filter(r=>r.instrument_key===compare.value).map(r=>r.rs55==null?{time:r.date}:{time:r.date,value:r.rs55}));price.timeScale().fitContent();rs.timeScale().fitContent();inspect(day.value);}
function inventory(){let q=filter.value.toLowerCase();table('inventory',[['Official / provider name','official_name'],['Classification','classification'],['Key','instrument_key'],['History','history_status'],['First','first_verified_provider_date'],['Last','last_verified_provider_date'],['Missing / next action','next_action']],D.inventory.filter(r=>(r.official_name+' '+r.provider_name+' '+r.classification).toLowerCase().includes(q)))}
window.TECH06={data:D,selectedDate:day.value,inspect,charts:{price,rs}};
day.onchange=()=>inspect(day.value);compare.onchange=plot;filter.oninput=inventory;
let syncing=false;for(const [a,b] of [[price,rs],[rs,price]]){a.timeScale().subscribeVisibleLogicalRangeChange(range=>{if(!syncing&&range){syncing=true;b.timeScale().setVisibleLogicalRange(range);syncing=false}});a.subscribeCrosshairMove(p=>{if(typeof p.time==='string')inspect(p.time)})}
plot();inventory();window.addEventListener('resize',()=>{price.resize(plotWidth(),300);rs.resize(plotWidth(),300)});function plotWidth(){return document.getElementById('plot').clientWidth}
</script></body></html>'''.replace('DATA',data)
    # The narrowed inventory is the current source when available. Old context is
    # retained only for distinct channels such as H10 and Indian index closes.
    if (dest/'global-market-data.js').exists():
        from experiments.technical_fix_v1.repair import SECTOR_BRIDGE
        text=text.replace('</body>', '\n<!-- TECH-FIX CURRENT CONTEXT -->'+SECTOR_BRIDGE+'<!-- END TECH-FIX CURRENT CONTEXT --></body>')
    (dest/'sector-rotation.html').write_text(text)
