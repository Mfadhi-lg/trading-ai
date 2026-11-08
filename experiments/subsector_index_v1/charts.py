"""Small closing-index adapter over copied ER03C assets and interactions.

Original templates/CSS/library/synchronization/navigation are reused. The price
series changes to LineSeries for closing indices; no composite O/H/L is created.
Stock examples retain actual candlesticks and add two peer RS lines/inspection.
"""
import json
import html
import shutil
from experiments.earnings_reaction_v1 import er03_chart_upgrade as chart
from experiments.earnings_reaction_v1.er03 import candle,write_json
from .sources import OUT,INPUT,MEMBERS,CUTOFFS,NAMES

def adapted_renderer():
    text=(chart.ASSETS/'chart.js').read_text()
    def replace(a,b):
        nonlocal text
        if text.count(a)!=1:raise ValueError('ER03C adapter anchor changed: '+a[:65])
        text=text.replace(a,b)
    replace("  const L=window.LightweightCharts", "  const isIndex=D.kind==='subsector-close-index';\n  const L=window.LightweightCharts")
    replace("  series.price=price.addSeries(L.CandlestickSeries,{upColor:'#26a69a',downColor:'#ef5350',borderVisible:false,wickUpColor:'#26a69a',wickDownColor:'#ef5350',priceLineVisible:false});\n  series.price.setData(rows.map(r=>({time:r.date,open:r.open,high:r.high,low:r.low,close:r.close})));",
       "  if(isIndex){series.price=price.addSeries(L.LineSeries,{color:'#d7e0eb',lineWidth:2,priceLineVisible:false});series.price.setData(rows.map(r=>r.index_close===null?{time:r.date}:{time:r.date,value:r.index_close}));}\n  else{series.price=price.addSeries(L.CandlestickSeries,{upColor:'#26a69a',downColor:'#ef5350',borderVisible:false,wickUpColor:'#26a69a',wickDownColor:'#ef5350',priceLineVisible:false});series.price.setData(rows.map(r=>({time:r.date,open:r.open,high:r.high,low:r.low,close:r.close})));}")
    replace("  line(dss,'dss_pct','#b47bea'", "  line(dss,isIndex?'return_20s_pct':'dss_pct','#b47bea'")
    a="  series.volume=volume.addSeries(L.HistogramSeries,{priceLineVisible:false,priceFormat:{type:'volume'}});"
    b="  if(isIndex){line(volume,'pct_above_ema20','#d7cba8');line(volume,'pct_rising','#54bca8');line(volume,'pct_rvol_gt1','#b47bea');series.volume=series.pct_above_ema20;}else{\n"+a
    replace(a,b)
    replace("  series.volume.priceScale().applyOptions({scaleMargins:{top:.3,bottom:.08}});", "  series.volume.priceScale().applyOptions({scaleMargins:{top:.3,bottom:.08}});\n  }")
    replace("  const zeroLine=series.rs55.createPriceLine", "  if(!isIndex){line(rs,'peer_rs55','#d7cba8',{title:'LOO peers RS55'});line(rs,'subsector_rs55','#8ea5c9',{title:'Full subset RS55'});}\n  const zeroLine=series.rs55.createPriceLine")
    replace("  const pp=rows.filter(r=>r.simple.pocket_pivot===true)", "  if(!isIndex){\n  const pp=rows.filter(r=>r.simple.pocket_pivot===true)")
    replace("  L.createSeriesMarkers(series.volume,pp.sort((a,b)=>a.time.localeCompare(b.time)));", "  L.createSeriesMarkers(series.volume,pp.sort((a,b)=>a.time.localeCompare(b.time)));\n  }")
    replace("  const anchors={price:series.price,dss:series.dss_pct,volume:series.volume,rs:series.rs55};", "  const anchors={price:series.price,dss:isIndex?series.return_20s_pct:series.dss_pct,volume:series.volume,rs:series.rs55};")
    replace("  const anchorKey={price:'close',dss:'dss_pct',volume:'volume',rs:'rs55'};", "  const anchorKey={price:'close',dss:isIndex?'return_20s_pct':'dss_pct',volume:isIndex?'pct_above_ema20':'volume',rs:'rs55'};")
    first="    document.getElementById('inspection').textContent="
    start=text.index(first);end=text.index('    state.crosshairs={};',start)
    old=text[start:end]
    sector="""    if(isIndex){
      document.getElementById('inspection').textContent=`${D.symbol} · ${r.date} | Closing index ${f(r.index_close,4)} points · EMA10 ${f(r.ema10,4)} / EMA20 ${f(r.ema20,4)} | ${r.observed_constituents}/${r.expected_constituents} constituents | ${r.period_role}`;
      document.getElementById('dss-value').textContent=`${r.date} · Return 5/10/20/40 sessions: ${[5,10,20,40].map(n=>f(r['return_'+n+'s_pct'])+'%').join(' / ')} · RS55 ${f(r.rs55,6)}`;
      document.getElementById('volume-value').textContent=`Breadth (%) · Above EMA20 ${f(r.pct_above_ema20)} (sand) · Rising ${f(r.pct_rising)} (green) · RVOL20 > 1 ${f(r.pct_rvol_gt1)} (purple) | Median RVOL ${f(r.median_constituent_rvol20,3)} | Close × volume value proxy ${f(r.traded_value_proxy_crore)} INR crore; not exact turnover`;
      document.getElementById('rs-value').textContent=`${r.date} · RS55 ${f(r.rs55,6)} vs NIFTYMIDSML400 · ${r.rs55_status} | ${r.members} | ${r.missing_data_flag||'Complete retained subset'} | RETROSPECTIVE_DESCRIPTIVE`;
    }else{
"""
    peer="""    }
    const context=document.getElementById('peer-inspection');
    if(context){context.textContent=isIndex?`${r.date} · Fixed members: ${r.members}. Daily equal weights. Corporate-action review: primary complete feed unavailable. No composite OHLC or share-volume sum.`:`${r.date} · LOO peers (${D.peer_members.join(', ')}): index ${f(r.peer_index_close,4)}, 20-session return ${f(r.peer_return20_pct)}%, RS55 ${f(r.peer_rs55,6)} (sand); full subset RS55 ${f(r.subsector_rs55,6)} (slate). Stock RS55 is green. ${D.peer_warning} RETROSPECTIVE_DESCRIPTIVE; later chart bars are outcome context.`;context.dataset.selectedDate=r.date;}
"""
    text=text[:start]+sector+old+peer+text[end:]
    replace("  const definitions=[['ema10'", "  const stockDefinitions=[['ema10'")
    replace("  for(const [id,title,status] of definitions){", "  const definitions=isIndex?[['ema10','EMA10','CLOSE_INDEX'],['ema20','EMA20','CLOSE_INDEX'],['dss','Momentum20','RETURN_PCT'],['volume','Breadth','PERCENT'],['rs','RS55','EXACT_ALIGNMENT']]:stockDefinitions;\n  for(const [id,title,status] of definitions){")
    replace("  const missingColors=rows.filter(r=>r.simple.original_final_color===null).length;", "  const missingColors=isIndex?0:rows.filter(r=>r.simple.original_final_color===null).length;")
    replace("  syncing=true;select(rows.length-1);syncing=false;", "  if(isIndex){document.getElementById('dtr-box').hidden=true;document.getElementById('provenance').textContent=D.availability_note+' '+D.record_context;}\n  syncing=true;select(rows.length-1);syncing=false;")
    return text

def render(candles,stocks,indices,loo,calendar,benchmark,features):
    dest=OUT/'charts';assets=dest/'assets';assets.mkdir(parents=True,exist_ok=True)
    for p in chart.ASSETS.iterdir():
        if p.is_file():shutil.copyfile(p,assets/p.name)
    (assets/'chart.js').write_text(adapted_renderer())
    template=(chart.ASSETS/'template.html').read_text().replace('Historical analysis · ER-04 paused','SS-INDEX-01 · RETROSPECTIVE_DESCRIPTIVE')
    template=template.replace('<main>','<div id="peer-inspection" class="readout" style="padding:10px 16px"></div><main>')
    links=[];names=[]
    def save(payload,name,initial=None,is_index=False):
        text=template
        if is_index:
            text=text.replace('Daily · NSE','Daily closing-price index').replace('Price · INR','Closing index · points (base 1,000)')
            text=text.replace('DSS_Rajput RVol · lookback 20 · %','Subsector momentum · 20-session return · %').replace('BEHAVIORALLY_RECONSTRUCTED · original startup unresolved','Actual closing-price index; 5/10/20/40 returns in inspection')
            text=text.replace('Simple Volume with Pocket Pivots · provider volume units','Constituent breadth · %').replace('BEHAVIORALLY_RECONSTRUCTED · PP10 / SMA21 / RVOL21 / U-D20 / turnover50','Above EMA20 sand · rising green · RVOL20 > 1 purple; no aggregate share volumes')
            a=text.index('<details><summary>');b=text.index('</details>',a)+len('</details>')
            text=text[:a]+'<details><summary>Membership and methodology</summary><div id="provenance"></div><p>Actual daily closes; fixed retrospective subset; daily 1/N return weights. Warmup before formation is a retrospective backcast. Complete primary corporate-action feed unavailable; see review evidence. No composite open, high or low is calculated.</p></details>'+text[b:]
        text=text.replace('__SYMBOL__',html.escape(payload['symbol'])).replace('__DATA__',chart.json_text(dict(payload,initial=initial)).replace('</','<\\/'))
        (dest/name).write_text(text);names.append(name);links.append((name,payload['symbol']))
    for (q,g),rows in indices.items():
        payload=dict(kind='subsector-close-index',symbol=NAMES[g]+' · '+q,rows=[dict(r,close=r['index_close']) for r in rows],
            events=[dict(date=CUTOFFS[q],plotted_session=next(d for d in calendar if d>CUTOFFS[q]),text='Formation cutoff '+CUTOFFS[q])],windows=[],labels=dict(high=[],low=[]),
            statuses=dict(ema='CLOSE_INDEX',dtr='UNAVAILABLE',dss='RETURN_PCT',simple='BREADTH_PCT',rs='EXACT_ALIGNMENT',profile='UNAVAILABLE'),
            record_context='Daily equal-weight three-company retained subset; not full subsector. '+('Extended mixed EMS/ODM/engineering exposure; pure-core benchmark insufficient.' if g=='EMS' else ''),
            availability_note='RETROSPECTIVE_DESCRIPTIVE. Base 1,000 at 2025-12-01. Before '+CUTOFFS[q]+' is membership-backcast warmup. Provider observation calendar, no missing-price filling.',profile_note='No synthetic profile or OHLC')
        save(payload,q+'-'+g+'-index.html',dict(first=CUTOFFS[q] if CUTOFFS[q] in calendar else next(d for d in calendar if d>CUTOFFS[q]),last=calendar[-1]),True)
    # Existing stock renderer math and chart payload assembly are reused intact.
    pilots={p['symbol']:p for p in __import__('experiments.earnings_reaction_v1.er03',fromlist=['read_csv']).read_csv(INPUT/'pilot-companies.csv')}
    bm={r['date']:candle(r['instrument_key'],[r['original_timestamp'],r['raw_open'],r['raw_high'],r['raw_low'],r['raw_close'],r['raw_volume'],0]) for r in candles if r['symbol']=='NIFTYMIDSML400'}
    for q in CUTOFFS:
        for g,syms in MEMBERS.items():
            for s in syms:
                p=pilots[s];raw=sorted([r for r in candles if r['symbol']==s],key=lambda r:r['date'])
                bars=tuple(candle(r['instrument_key'],[r['original_timestamp'],r['raw_open'],r['raw_high'],r['raw_low'],r['raw_close'],r['raw_volume'],0]) for r in raw)
                payload=chart.assemble(s,p['instrument_key'],bars,bm)
                peer={r['date']:r for r in loo[q,s]};full={r['date']:r for r in indices[q,g]}
                for r in payload['rows']:
                    f=peer.get(r['date'],{});r.update(peer_index_close=f.get('index_close'),peer_return20_pct=f.get('return_20s_pct'),peer_rs55=f.get('rs55'),subsector_rs55=full.get(r['date'],{}).get('rs55'))
                payload.update(peer_members=[x for x in syms if x!=s],peer_warning='Only two peers. '+('Extended heterogeneous EMS exposure.' if g=='EMS' else 'Retained subset, not complete sector.'))
                checkpoints=[f for f in features if f['symbol']==s and f['quarter']==q and f['status']=='AVAILABLE_RETROSPECTIVE']
                payload['events'] += [dict(date=f['checkpoint_date'],plotted_session=f['checkpoint_date'],text=f"S{f['checkpoint']} close") for f in checkpoints]
                payload['events'].sort(key=lambda e:e.get('plotted_session') or '')
                f=next(f for f in checkpoints if f['checkpoint']==3)
                save(payload,q+'-'+s+'-peers.html',dict(first=f['results_date'],last=f['checkpoint_date']))
    (dest/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>SS-INDEX-01 charts</title><h1>Historical subsector closing indices and leave-one-out peers</h1><p>Existing ER03C renderer; three retained subsets; two fixed formation dates. RETROSPECTIVE_DESCRIPTIVE. Read ../METHODOLOGY.md and ../RESEARCH_FINDINGS.md.</p>'+''.join(f'<p><a href="{html.escape(n)}">{html.escape(label)}</a></p>' for n,label in links))
    write_json(OUT/'chart-index.json',dict(charts=names,count=len(names),renderer='Copied ER03C template, CSS, library, crosshair and navigation; closing-index LineSeries adapter; original source assets unmodified'))
    return names
