"""V2 payloads for the existing ER03C synchronized four-panel renderer."""
import html, shutil
from experiments.earnings_reaction_v1 import er03_chart_upgrade as er
from experiments.earnings_reaction_v1.er03 import write_json
from experiments.subsector_index_v1.charts import adapted_renderer
from .prepare import OUT
from .build import SETS,NAMES,PRIMARY

def render(candles,stocks,indices,loo,calendar,benchmark,nifty,features):
    dest=OUT/'charts';assets=dest/'assets';assets.mkdir(parents=True,exist_ok=True)
    for p in er.ASSETS.iterdir():
        if p.is_file():shutil.copyfile(p,assets/p.name)
    js=adapted_renderer()
    # Keep original panel creation, crosshair synchronization, navigation and controls.
    anchor="  line(dss,isIndex?'return_20s_pct':'dss_pct','#b47bea'"
    start=js.index(anchor);end=js.index('\n',start)
    js=js[:start]+"  line(dss,'normalized_stock','#eeeeee');line(dss,'normalized_subsector','#d7cba8');line(dss,'normalized_peer','#54bca8');line(dss,'normalized_nifty50','#8ea5c9');line(dss,'normalized_midsml400','#b47bea');line(dss,'normalized_sector_ema10','#75aaff');line(dss,'normalized_sector_ema20','#e8a65d');series.return_20s_pct=series.normalized_subsector;\n"+js[end:]
    js=js.replace("  const zeroLine=series.rs55.createPriceLine", "  line(rs,'peer_rs55','#d7cba8');line(rs,'subsector_rs55','#8ea5c9');\n  const zeroLine=series.rs55.createPriceLine")
    js=js.replace("dss:isIndex?'return_20s_pct':'dss_pct'", "dss:isIndex?'normalized_subsector':'dss_pct'")
    js=js.replace("context.dataset.selectedDate=r.date;", "context.textContent += ` | Nifty50 ${f(r.nifty50_close)} · midsml400 ${f(r.midsml400_close)} | Subsector close ${f(r.subsector_close,4)} · EMA10/20 ${f(r.subsector_ema10,4)}/${f(r.subsector_ema20,4)} | Core peer RS55 ${f(r.peer_rs55,6)} | Activity ratio20 ${f(r.median_constituent_traded_value_ratio20,3)} · Largest abs daily return contribution ${f(r.largest_absolute_return_contribution_pct)}% | ${D.provenance}`;context.dataset.selectedDate=r.date;")
    js=js.replace("D.peer_members.join(', ')","(D.peer_members||[]).join(', ')")
    js=js.replace("Closing index ${f(r.index_close,4)} points", "${D.stock_chart?'Stock close':'Closing index'} ${f(r.index_close,4)} ${D.stock_chart?'INR':'points'}")
    js=js.replace("${r.date} · Return 5/10/20/40 sessions:", "${r.date} · Peer return 5/10/20/40 sessions:")
    js=js.replace("['dss','Momentum20','RETURN_PCT']", "['dss','Rebased comparisons','POINTS']")
    js=js.replace('APPROXIMATE · first/last observed sessions','Subsequent outcome window · event-session checkpoints')
    (assets/'chart.js').write_text(js)
    template=(er.ASSETS/'template.html').read_text().replace('Historical analysis · ER-04 paused','SS-INDEX-02 · provisional retrospective')
    template=template.replace('<main>','<div id="peer-inspection" class="readout" style="padding:10px 16px"></div><main>')
    template=template.replace('DSS_Rajput RVol · lookback 20 · %','Rebased closing prices · points (1 December 2025 = 1,000)')
    template=template.replace('Simple Volume with Pocket Pivots · provider volume units','Peer / subsector participation · %')
    template=template.replace('BEHAVIORALLY_RECONSTRUCTED · PP10 / SMA21 / RVOL21 / U-D20 / turnover50','Above EMA20 sand · advancing green · RVOL20 > 1 purple')
    template=template.replace('BEHAVIORALLY_RECONSTRUCTED · original startup unresolved','Stock white · subsector sand · independent peers green · Nifty50 slate · midsml400 purple · sector EMA10 blue / EMA20 orange')
    template=template.replace('SOURCE_VALIDATED · ratio of returns · zero baseline','Stock green · independent peers sand · full subsector slate · zero baseline')
    a=template.index('<details><summary>');b=template.index('</details>',a)+len('</details>')
    template=template[:a]+'<details><summary>Provenance and limitations</summary><div id="provenance"></div><p>Daily equal-return weights; fixed retrospective membership; base 1,000 on 2025-12-01. No certified historical selection, no full primary corporate-action audit. No synthetic index OHLC. Ordinary dividends not reinvested. Full chart includes subsequent outcomes; checkpoints use only their price prefix. Read VALIDATION_REPORT.md.</p></details>'+template[b:]
    strengths={(r['quarter'],r['subsector'],r['date']):r for r in __import__('experiments.earnings_reaction_v1.er03',fromlist=['read_csv']).read_csv(OUT/'SUBSECTOR_STRENGTH.csv')}
    names=[];links=[]
    def payload_rows(q,g,s=None):
        rows=[];peer=loo[q,g,s] if s else indices[q,g];sector=indices[q,g]
        base=stocks[s][0]['adjusted_close'] if s else None
        for i,r in enumerate(peer):
            day=r['date'];full=sector[i];sr=stocks[s][i] if s else full
            close=sr['adjusted_close'] if s else full['index_close']
            x=dict(r,index_close=close,close=close,ema10=sr['ema10'],ema20=sr['ema20'],
                normalized_stock=1000*close/base if s else None,normalized_subsector=full['index_close'],normalized_peer=r['index_close'] if s else None,
                normalized_sector_ema10=full['ema10'],normalized_sector_ema20=full['ema20'],
                normalized_nifty50=1000*nifty[day]/nifty[calendar[0]] if day in nifty else None,
                normalized_midsml400=1000*benchmark[day]/benchmark[calendar[0]],nifty50_close=nifty.get(day),midsml400_close=benchmark[day],
                subsector_close=full['index_close'],subsector_ema10=full['ema10'],subsector_ema20=full['ema20'],
                rs55=sr['rs55'],subsector_rs55=full['rs55'],peer_rs55=r['rs55'],
                period_role=full['period_role'],largest_absolute_return_contribution_pct=__import__('decimal').Decimal(strengths[q,g,day]['largest_absolute_return_contribution_pct']) if strengths[q,g,day]['largest_absolute_return_contribution_pct'] else None)
            rows.append(x)
        return rows
    def save(name,label,rows,events,windows,initial=None,stock=False):
        payload=dict(kind='subsector-close-index',stock_chart=stock,symbol=label,rows=rows,events=events,windows=windows,labels=dict(high=[],low=[]),
            statuses=dict(ema='ACTUAL_CLOSE_EMA',dtr='UNAVAILABLE',dss='NORMALIZED_COMPARISON',simple='PEER_BREADTH',rs='EXACT_ALIGNMENT',profile='UNAVAILABLE'),
            provenance='Raw provider hashes: DATA_COVERAGE.csv; member/business/adjustment evidence: HISTORICAL_ELIGIBILITY.csv; Nifty50 retained through '+max(nifty),
            availability_note='RETROSPECTIVE_DESCRIPTIVE_PROVISIONAL; no institutional-flow inference.',record_context='Existing ER03C renderer; synchronized date inspection.',
            profile_note='Unavailable',peer_members=rows[0]['members'].split(';'))
        text=template.replace('Price · INR','Stock closing price · INR' if stock else 'Subsector closing index · points')
        text=text.replace('__SYMBOL__',html.escape(label)).replace('__DATA__',er.json_text(dict(payload,initial=initial)).replace('</','<\\/'))
        (dest/name).write_text(text);names.append(name);links.append((name,label))
    for (q,g),rows in indices.items():
        save(q+'-'+g+'-index.html',NAMES[g]+' · '+q,payload_rows(q,g),[],[],dict(first=rows[0]['formation_cutoff'],last=calendar[-1]))
    eventdata=__import__('json').loads((__import__('experiments.subsector_index_v2.prepare',fromlist=['V1']).V1/'inputs/EVENTS.json').read_text())
    for e in eventdata:
        s=e['symbol'];g=next((g for g in PRIMARY if s in (SETS['EMS_BROAD'] if g=='EMS' else SETS['TYRES_WITH_RETREAD'] if g=='TYRES' else SETS['TRANSFORMERS_BROAD'])),None)
        if not g:continue
        q=e['quarter'];fs=[f for f in features if f['event_id']==e['event_id'] and f['status']=='AVAILABLE_RETROSPECTIVE']
        events=[dict(date=e['reported_results_date'],plotted_session=next(d for d in calendar if d>e['reported_results_date']),text='Earnings date; timing unverified')]
        events += [dict(date=f['checkpoint_date'],plotted_session=f['checkpoint_date'],text='S'+str(f['checkpoint'])+' close') for f in fs]
        windows=[dict(event_id=e['event_id']+' S3 to S'+str(f['checkpoint'])+' subsequent outcome',observed_first_date=next(x['checkpoint_date'] for x in fs if x['checkpoint']==3),observed_last_date=f['checkpoint_date']) for f in fs if f['checkpoint'] in (20,40)]
        save(q+'-'+s+'-event.html',s+' vs '+g+' V2 · '+q,payload_rows(q,g,s),events,windows,dict(first=e['reported_results_date'],last=fs[-1]['checkpoint_date']),True)
    (dest/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>SS-INDEX-02 charts</title><h1>V2 daily subsector and stock comparisons</h1><p>Provisional retrospective indices. Core and broader sensitivities are separate. All four panels synchronize on date. Earnings S1/3/5/20/40 markers identify early checkpoints and later outcome periods. Nifty50 retained through '+max(nifty)+'; no filling. <a href="../VALIDATION_REPORT.md">Validation and limitations</a></p>'+''.join('<p><a href="'+n+'">'+html.escape(label)+'</a></p>' for n,label in links))
    write_json(OUT/'chart-index.json',dict(charts=names,count=len(names),renderer='Existing ER03C four-panel renderer and V1 closing-line adapter; V2 payload and comparison lines'))
    return names
