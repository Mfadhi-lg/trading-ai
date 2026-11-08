"""Copy/enrich existing ER03C and V2 charts; no financial indicator recomputation."""
import json,re,shutil,html
from pathlib import Path
from decimal import Decimal as D
from .prepare import ROOT,OUT,V2
from experiments.earnings_reaction_v1.er03 import read_csv,write_json,digest
from experiments.earnings_reaction_v1.er03_chart_upgrade import json_text

def unpack(text):
    match=re.search(r'(<script id="chart-data" type="application/json">)(.*?)(</script>)',text,re.S)
    if not match:raise ValueError('Original chart payload missing')
    return json.loads(match[2]),match

def adapter(js,stock):
    creation="  const price=make('price'),dss=make('dss'),volume=make('volume'),rs=make('rs');"
    if js.count(creation)!=1:raise ValueError('ER03C chart creation anchor changed')
    js=js.replace(creation,creation+"\n  const activity=make('activity');"+("const comparison=make('comparison'),participation=make('participation');" if stock else ''))
    anchor="  const profileSeries=[];"
    extra="""
  series.tech_activity=activity.addSeries(L.HistogramSeries,{priceLineVisible:false,priceFormat:{type:'price',precision:2,minMove:.01},title:'Estimated traded value (INR crore)'});
  const activityColors={GREEN:'#26a69a',RED:'#ef5350',NEUTRAL:'#8996a6'};
  series.tech_activity.setData(rows.map(r=>r.tech?.value==null?{time:r.date}:{time:r.date,value:r.tech.value,color:activityColors[r.tech.color]}));
  line(activity,'tech.sma20','#d7cba8',{title:'Activity SMA20 incl current (INR crore)'});
"""
    if stock:
        extra+="""
  line(comparison,'tech.normalized_stock','#eeeeee');line(comparison,'tech.normalized_subsector','#d7cba8');line(comparison,'tech.normalized_nifty50','#8ea5c9');line(comparison,'tech.normalized_official','#b47bea');
  line(rs,'tech.loo_rs55','#d7cba8',{title:'Independent core peers RS55'});
  line(participation,'tech.pct_above','#d7cba8');line(participation,'tech.pct_advancing','#54bca8');line(participation,'tech.pct_activity','#b47bea');
"""
    else:extra+="  line(dss,'tech.normalized_official','#ffcf72',{title:'Official broad sector index'});\n"
    if js.count(anchor)!=1:raise ValueError('ER03C profile anchor changed')
    js=js.replace(anchor,extra+'\n'+anchor)
    lines=js.splitlines()
    for i,line in enumerate(lines):
        if 'const anchors={' in line:lines[i]=line.replace('};',",activity:series.tech_activity"+(",comparison:series['tech.normalized_subsector'],participation:series['tech.pct_above']" if stock else '')+'};')
        if 'const anchorKey={' in line:lines[i]=line.replace('};',",activity:'tech.value'"+(",comparison:'tech.normalized_subsector',participation:'tech.pct_above'" if stock else '')+'};')
    js='\n'.join(lines)+'\n'
    hook="const r=rows[state.selected],v=r.simple;"
    if js.count(hook)!=1:raise ValueError('ER03C date selection anchor changed')
    js=js.replace(hook,hook+'\n    window.renderTechnical(r.date,r.tech,D.technical_context);')
    defs="  for(const [id,title,status] of definitions){"
    js=js.replace(defs,"  definitions.push(['activity','Estimated activity','INR_CRORE']);"+("definitions.push(['comparison','Benchmarks','REBASED_CLOSE'],['participation','Subsector breadth','PERCENT']);" if stock else '')+'\n'+defs)
    return js

TECH_JS="""'use strict';
(()=>{
 const f=v=>v==null||v===''?'UNAVAILABLE':typeof v==='number'?v.toLocaleString('en-IN',{maximumFractionDigits:3}):String(v);
 window.renderTechnical=(day,t,contexts)=>{
  const a=document.getElementById('activity-value');
  a.dataset.selectedDate=day;
  a.textContent=day+' | Estimated raw close × traded shares: '+f(t?.value)+' INR crore; not exact turnover | SMA20 including current: '+f(t?.sma20)+' | Prior20 completed mean: '+f(t?.preceding20)+' | Relative GROUP activity: '+f(t?.relative20)+'× | Median MEMBER RVOL20: '+f(t?.median_rvol20)+'× (separate measure) | Member RVOL>1: '+f(t?.pct_activity)+'% | Advancing: '+f(t?.pct_advancing)+'% | Above EMA20: '+f(t?.pct_above)+'% | Price-index change: '+f(t?.price_change)+'% | Coverage: '+f(t?.observed)+'/'+f(t?.expected)+' | Independent peers RS55: '+f(t?.loo_rs55)+' | Official benchmark: '+(t?.official||'UNAVAILABLE')+'. Bars show index direction, not buy/sell volume or institutional flows.';
  const panel=document.getElementById('market-context');panel.dataset.selectedDate=day;
  panel.replaceChildren();const summary=document.createElement('summary');summary.textContent=day+' · Indian PREOPEN 09:15 exclusive / POSTCLOSE 18:00 IST · provenance and availability';panel.append(summary);
  const note=document.createElement('p');note.textContent='Verified-release features, conceptual Indian closes and retrospective observations are separate. Current-vintage crude is spot context, not settlement. US provider dates are unresolved and quarantined. Futures snapshots are unavailable. Missing is not zero.';panel.append(note);
  const table=document.createElement('table');table.style.width='100%';table.style.fontSize='11px';
  const header=document.createElement('tr');for(const name of ['Series / units','PREOPEN verified release-bound','POSTCLOSE verified release-bound','Prior Indian close (context)','Same-session Indian close (context)','Retrospective only / change','Status / provenance']){const th=document.createElement('th');th.textContent=name;header.append(th);}table.append(header);
  const records=(contexts||window.TECH_CONTEXT)?.[day]||[];const codes=[...new Set(records.map(r=>r.series))];
  for(const code of codes){const pre=records.find(r=>r.series===code&&r.phase==='PREOPEN');const post=records.find(r=>r.series===code&&r.phase==='POSTCLOSE');const r=post||pre;
   const tr=document.createElement('tr');const cells=[code+' / '+r.unit,f(pre?.verified_feature_value)+(pre?.verified_observation_date?' ('+pre.verified_observation_date+')':'')+' / Δ '+f(pre?.verified_change_1s_pct)+'%',f(post?.verified_feature_value)+(post?.verified_observation_date?' ('+post.verified_observation_date+')':'')+' / Δ '+f(post?.verified_change_1s_pct)+'%',f(pre?.conservative_prior_close_context_value)+(pre?.prior_close_date?' ('+pre.prior_close_date+')':''),f(post?.same_session_close_context_value),f(r.retrospective_value)+(r.retrospective_provider_date?' ('+r.retrospective_provider_date+')':'')+' / '+f(r.retrospective_change_1s_pct)+'%',r.status+'; age '+f(r.age_calendar_days)+' calendar days'];
   for(const [i,val] of cells.entries()){const td=document.createElement('td');td.textContent=val;td.style.borderBottom='1px solid #334154';td.style.padding='5px';const source=i===5?r.retrospective_source_file:i===3?pre?.prior_close_source_file:i===4?post?.same_session_source_file:i===1?pre?.source_file:r.source_file;const sha=i===5?r.retrospective_source_sha256:i===3?pre?.prior_close_source_sha256:i===4?post?.same_session_source_sha256:i===1?pre?.source_sha256:r.source_sha256;td.title=source+' | '+sha+' | '+(i===5?r.retrospective_vintage:r.availability_basis)+' | original '+r.original_provider_timestamp+' | verified availability '+r.verified_available_at;tr.append(td);}table.append(tr);
  }panel.append(table);
 };
})();
"""

def render(activity,sectors,pre,post):
    dest=OUT/'charts';names=[];ledger=[]
    by={(r['quarter'],r['subsector'],r['date']):r for r in activity}
    sector={(r['quarter'],r['subsector'],r['date']):r for r in sectors}
    loo={(r['quarter'],r['subsector'],r['symbol'],r['date']):r for r in read_csv(V2/'LEAVE_ONE_OUT.csv')}
    context={}
    fields=('phase','series','unit','verified_feature_value','verified_observation_date','verified_change_1s_pct','conservative_prior_close_context_value','prior_close_date','same_session_close_context_value','retrospective_value','retrospective_provider_date','retrospective_change_1s_pct','status','age_calendar_days','source_file','source_sha256','availability_basis','original_provider_timestamp','verified_available_at','retrospective_source_file','retrospective_source_sha256','retrospective_vintage','prior_close_source_file','prior_close_source_sha256','same_session_source_file','same_session_source_sha256')
    for r in pre+post:context.setdefault(r['indian_session'],[]).append({k:r.get(k) for k in fields})
    def enrich(text,q,g,s=None):
        data,match=unpack(text);original=data['rows'];base_stock=next((r['close'] for r in original if r['date']=='2025-12-01'),None)
        base_sector=sector.get((q,g,'2025-12-01'));obase=base_sector['official_close'] if base_sector else None
        for r in original:
            day=r['date'];a=by.get((q,g,day));b=sector.get((q,g,day));p=loo.get((q,g,s,day)) if s else None
            r['tech']=dict(value=a['estimated_traded_value_crore'],sma20=a['activity_sma20_including_current_crore'],preceding20=a['preceding20_mean_activity_crore'],
                relative20=a['relative_group_activity20'],median_rvol20=a['median_constituent_rvol20'],pct_activity=a['pct_activity_expanding'],pct_advancing=a['pct_advancing'],
                pct_above=a['pct_above_ema20'],price_change=a['index_return_1s_pct'],observed=a['observed_constituents'],expected=a['expected_constituents'],color=a['histogram_color'],
                normalized_official=1000*b['official_close']/obase if obase and b['official_close'] is not None else None,
                official=b['official_series']+' ('+b['status']+')',
                normalized_stock=1000*D(str(r['close']))/D(str(base_stock)) if s and base_stock else None,
                normalized_subsector=b['custom_index_close'],normalized_nifty50=1000*b['nifty50_close']/base_sector['nifty50_close'] if base_sector and b['nifty50_close'] else None,
                loo_rs55=D(p['peer_rs55']) if p and p['peer_rs55'] else None,loo_members=p['peer_members'] if p else '') if a and b else None
        text=text[:match.start(2)]+json_text(data).replace('</','<\\/')+text[match.end(2):]
        panel='<section id="activity-panel"><div class="panel-title">Estimated constituent traded value · INR crore <span>Raw close × shares proxy · SMA20 sand · green/red = index direction</span></div><div class="chart" id="activity" style="height:190px"></div><div class="readout" id="activity-value"></div></section>'
        if s:
            panel+='<section id="participation-panel"><div class="panel-title">Subsector participation · % <span>Above EMA20 sand · advancing green · member RVOL20 > 1 purple; separate from stock share-volume</span></div><div class="chart" id="participation" style="height:160px"></div></section>'
            panel+='<section id="comparison-panel"><div class="panel-title">Daily closing-price comparisons · 1 Dec 2025 = 1,000 <span>Stock white · core subsector sand · Nifty50 slate · official broad sector purple</span></div><div class="chart" id="comparison" style="height:170px"></div></section>'
        else:text=text.replace('sector EMA10 blue / EMA20 orange','sector EMA10 blue / EMA20 orange · official broad sector amber')
        text=text.replace('</main>',panel+'</main><details id="market-context"></details>')
        text=text.replace('<script src="assets/chart.js"></script>','<script src="assets/technical-context.js"></script><script src="assets/technical.js"></script><script src="assets/chart.js"></script>')
        text=text.replace('Read VALIDATION_REPORT.md','Read ../../TECHNICAL_ACCEPTANCE.md')
        return text
    for kind,source,stock in [('indices',V2/'charts',False),('stocks',ROOT/'artifacts/earnings-reaction-v1/er04/charts',True)]:
        target=dest/kind;assets=target/'assets';shutil.copytree(source/'assets',assets,dirs_exist_ok=True)
        (assets/'chart.js').write_text(adapter((source/'assets/chart.js').read_text(),stock));(assets/'technical.js').write_text(TECH_JS)
        (assets/'technical-context.js').write_text('window.TECH_CONTEXT='+json_text(context)+';\n')
        if not stock:
            specs=[(p,p.name.split('-')[0]+'-'+p.name.split('-')[1],p.name.split('-',2)[2].removesuffix('-index.html'),None) for p in sorted(source.glob('*-index.html'))]
        else:specs=[(source/('FY2027-Q1-'+s+'-S3.html'),'FY2027-Q1',g,s) for s,g in [('CEATLTD','TYRES'),('PGEL','EMS'),('VOLTAMP','TRANSFORMERS')]]
        for p,q,g,s in specs:
            before,unused=unpack(p.read_text());text=enrich(p.read_text(),q,g,s);name=kind+'/'+p.name
            (dest/name).write_text(text);names.append(name)
            after,unused=unpack(text)
            # Every pre-existing numerical stock/indicator field must survive verbatim.
            if stock:
                stripped=[{k:v for k,v in r.items() if k!='tech'} for r in after['rows']]
                if stripped!=before['rows']:raise ValueError('Original stock/indicator payload altered')
            ledger.append(dict(chart=name,original_source=str(p.relative_to(ROOT)),original_sha256=digest(p),stock_indicator_rows_preserved=len(before['rows']) if stock else None))
    for kind in ('indices','stocks'):
        (dest/kind/'index.html').write_text('<!doctype html><meta charset="utf-8"><h1>TECH-05 '+kind+'</h1>'+''.join('<p><a href="'+html.escape(Path(n).name)+'">'+html.escape(Path(n).stem)+'</a></p>' for n in names if n.startswith(kind+'/')))
    (dest/'index.html').write_text('<!doctype html><meta charset="utf-8"><title>TECH-05 daily integration</title><h1>Daily technical integration</h1><p>Existing ER03C and V2 renderer extensions; estimated activity is separate from breadth and stock share volume. Click a date to inspect context and provenance. US bucket dates are quarantined; futures unavailable. <a href="../TECHNICAL_ACCEPTANCE.md">Acceptance and limitations</a></p>'+''.join('<p><a href="'+html.escape(n)+'">'+html.escape(n)+'</a></p>' for n in names))
    write_json(OUT/'chart-index.json',dict(charts=names,count=len(names),renderer='Existing ER03C/V2 chart.js copied and extended with synchronized panels; no indicator mathematics rebuilt',provenance=ledger))
    return names
