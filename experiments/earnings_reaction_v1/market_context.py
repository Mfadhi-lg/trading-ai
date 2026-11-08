"""ER04 extension: explicit vintages, IST checkpoints and unchanged ER03C charts.

No network calls. No model fitting. Current-vintage oil/FX never enter causal
checkpoint features. Daily settlements never stand in for futures snapshots.
"""
from datetime import date, datetime, time, timedelta
from decimal import Decimal as D
from zoneinfo import ZoneInfo
from statistics import median
from pathlib import Path
import csv
import html
import json
import re
import shutil
import zipfile
from .er03 import ROOT, BASE, BENCH, read_csv, write_csv, write_json, digest
from .er04 import OUT as ER04, COMPLETION, load_data, preservation

OUT=ER04/'market-context'
IST=ZoneInfo('Asia/Kolkata'); NY=ZoneInfo('America/New_York')
SERIES=('SP500','NASDAQCOM','DJIA','ES','NQ','BRENT','WTI','USDINR','NIFTY50','INDIA_VIX','GIFT_NIFTY')
HOLIDAYS_2026={date(2026,1,1),date(2026,1,19),date(2026,2,16),date(2026,4,3),date(2026,5,25),date(2026,6,19),date(2026,7,3),date(2026,9,7),date(2026,11,26),date(2026,12,25)}
SOURCES={
 'SP500':'https://fred.stlouisfed.org/series/SP500',
 'NASDAQCOM':'https://fred.stlouisfed.org/series/NASDAQCOM',
 'DJIA':'https://fred.stlouisfed.org/series/DJIA',
 'ES':'https://www.cmegroup.com/market-data/real-time-and-historical-data.html',
 'NQ':'https://www.cmegroup.com/market-data/real-time-and-historical-data.html',
 'BRENT':'https://fred.stlouisfed.org/series/DCOILBRENTEU',
 'WTI':'https://fred.stlouisfed.org/series/DCOILWTICO',
 'USDINR':'https://www.federalreserve.gov/releases/h10/about.htm',
 'NIFTY50':'https://upstox.com/developer/api-documentation/v3/historical-candle-data/',
 'INDIA_VIX':'https://www.nseindia.com/reports-indices-historical-vix',
 'GIFT_NIFTY':'https://www.nseix.com/' }
MISSING={s:'NOT_RETAINED;LICENSE_OR_ENTITLEMENT_UNCONFIRMED' for s in SERIES}
MISSING.update(BRENT='PUBLICATION_TIMESTAMP_UNAVAILABLE;CURRENT_VINTAGE_ONLY',WTI='PUBLICATION_TIMESTAMP_UNAVAILABLE;CURRENT_VINTAGE_ONLY',
    INDIA_VIX='PUBLIC_NSE_TRANSPORT_FAILED;NO_BYPASS',GIFT_NIFTY='TIMESTAMPED_CONTRACT_HISTORY_UNAVAILABLE',
    ES='NO_HISTORICAL_PREOPEN_QUOTES;LICENSE_REQUIRED',NQ='NO_HISTORICAL_PREOPEN_QUOTES;LICENSE_REQUIRED',
    USDINR='NO_RELEASE_AVAILABLE_IN_RETAINED_ARCHIVE_RANGE',NIFTY50='NO_EXACT_SESSION_IN_RETAINED_RANGE')

def stamp(day,hour,minute=0,tz=IST):
    return datetime.combine(date.fromisoformat(day) if isinstance(day,str) else day,time(hour,minute),tz)

def previous_us_session(day):
    """Calendar only, NOT an index observation or provider-publication claim."""
    d=date.fromisoformat(day)-timedelta(days=1)
    if d.year!=2026: return None
    while d.weekday()>4 or d in HOLIDAYS_2026: d-=timedelta(days=1)
    hour=13 if d in {date(2026,11,27),date(2026,12,24)} else 16
    return stamp(d,hour,tz=NY)

def h10_rows(path):
    text=path.read_text()
    release=re.search(r'Release Date:\s*([A-Za-z]+\s+\d+,\s+\d{4})',text)
    if not release:raise ValueError('H10 release date missing')
    released=datetime.strptime(release[1],'%B %d, %Y').date()
    if path.stem!='H10-'+released.isoformat():raise ValueError('Archive identity mismatch')
    headings=re.findall(r'<th id="a[3-7]">\s*([A-Za-z.]+\s+\d+)\s*</th>',text)
    row=re.search(r'<tr>\s*<th[^>]*>\s*INDIA\s*</th>(.*?)</tr>',text,re.S)
    values=re.findall(r'<td[^>]*>\s*([^<]+?)\s*</td>',row[1])[1:]
    if len(values)!=5 or len(headings)!=5:raise ValueError('H10 schema')
    result=[]
    for heading,value in zip(headings,values):
        heading=heading.replace('.','').replace('Sept ','Sep ')
        d=datetime.strptime(heading+f' {released.year}','%b %d %Y').date()
        if d>released:d=d.replace(year=d.year-1)
        if value.strip()=='ND':continue
        observation=stamp(d,12,tz=NY)
        scheduled=stamp(released,16,15,NY)
        # A dated archive does not prove its actual intraday publication minute.
        # Use end of that NY date as a conservative availability upper bound.
        # In summer this is 09:30 IST next day, AFTER India's 09:15 cutoff.
        available=stamp(released+timedelta(days=1),0,tz=NY)
        assert observation<available
        result.append(dict(series='USDINR',observation_date=d.isoformat(),value=D(value.strip()),unit='INR per USD',
            observation_timestamp=observation.isoformat(),observation_timestamp_basis='NOON_NY_OFFICIAL_H10_CONVENTION',
            available_at=available.isoformat(),scheduled_release_at=scheduled.isoformat(),actual_publication_timestamp='',
            availability_basis='CONSERVATIVE_END_OF_DATED_RELEASE_DAY_NY;UPPER_BOUND_NOT_ACTUAL_PUBLICATION',
            source_url=f'https://www.federalreserve.gov/releases/h10/{released:%Y%m%d}/',
            source_file=str(path.relative_to(ROOT)),source_sha256=digest(path),vintage='DATED_H10_UNREVISED',
            adjustment='UNADJUSTED_SPOT;NOT_LIVE_OR_INDIAN_CLOSE',missing_data_flag='ACTUAL_RELEASE_MINUTE_UNAVAILABLE;LIVE_RATE_UNAVAILABLE',timestamp_confidence='CONSERVATIVE_DATED_RELEASE_BOUND'))
    return result

def changes(rows):
    rows.sort(key=lambda r:r['observation_date'])
    if len({r['observation_date'] for r in rows})!=len(rows):raise ValueError('Duplicate dates')
    for i,r in enumerate(rows):
        for n in (1,5,20):
            r[f'change_{n}s_pct']=100*(r['value']/rows[i-n]['value']-1) if i>=n else None
            r[f'base_{n}s_date']=rows[i-n]['observation_date'] if i>=n else ''
        r['horizon_basis']='PREVIOUS_1_5_20_VALID_OBSERVATIONS;OBSERVED_BUSINESS_SESSIONS_NOT_CERTIFIED_COMPLETE'
        r['direction_1s']=direction(r['series'],r['change_1s_pct'])
    return rows

def direction(series,value):
    if value is None:return 'UNAVAILABLE'
    if value==0:return 'UNCHANGED'
    if series=='USDINR':return 'RUPEE_DEPRECIATING' if value>0 else 'RUPEE_APPRECIATING'
    if series in ('BRENT','WTI'):return 'OIL_RISING' if value>0 else 'OIL_FALLING'
    if series=='INDIA_VIX':return 'VOLATILITY_INCREASING' if value>0 else 'VOLATILITY_DECREASING'
    return 'STRENGTHENING' if value>0 else 'WEAKENING'

def observations():
    data,provenance=load_data()
    ledger=json.loads((OUT/'public-acquisition-ledger.json').read_text())
    for r in ledger['requests']:
        if r.get('success') and digest(OUT/'raw'/r['file'])!=r['sha256']:raise ValueError('Public response hash mismatch')
    groups={s:[] for s in SERIES}; retrospective=[]
    for code,s,unit in [('DCOILBRENTEU','BRENT','USD per barrel'),('DCOILWTICO','WTI','USD per barrel'),('DEXINUS','USDINR','INR per USD')]:
        p=OUT/'raw'/(code+'.csv')
        if not p.exists():continue
        rows=[]
        for r in read_csv(p):
            v=r[code];day=r.get('observation_date',r.get('DATE'))
            # Source gaps are retained explicitly, never interpolated.
            base=dict(series=s,observation_date=day,value=D(v) if v not in ('','.','NA') else None,unit=unit,
                observation_timestamp='',observation_timestamp_basis='DATE_ONLY;EXACT_OBSERVATION_TIME_UNAVAILABLE',available_at='',
                availability_basis='NO_HISTORICAL_RELEASE_TIMESTAMP;RETROSPECTIVE_ONLY',
                source_url=f'https://fred.stlouisfed.org/series/{code}',source_file=str(p.relative_to(ROOT)),source_sha256=digest(p),
                vintage='CURRENT_AT_RETRIEVAL',adjustment='NOT_SEASONALLY_ADJUSTED;SPOT_NOT_FUTURES_SETTLEMENT',
                missing_data_flag='PUBLICATION_TIMESTAMP_UNAVAILABLE'+(';SOURCE_NULL' if v in ('','.','NA') else ''),timestamp_confidence='UNAVAILABLE')
            if base['value'] is None:
                for n in (1,5,20):base[f'change_{n}s_pct']=None;base[f'base_{n}s_date']=''
                base.update(horizon_basis='SOURCE_NULL;NO_IMPUTATION',direction_1s='UNAVAILABLE')
                retrospective.append(base)
            else:rows.append(base)
        retrospective+=changes(rows)
    for p in sorted((OUT/'raw').glob('H10-*.html')):groups['USDINR']+=h10_rows(p)
    changes(groups['USDINR'])
    nkey='NSE_INDEX|Nifty 50'
    # Reuse original retained candles, not a reconstructed benchmark proxy.
    for c in data[nkey]:
        day=c.start_ist.date()
        groups['NIFTY50'].append(dict(series='NIFTY50',observation_date=str(day),value=c.close,unit='index points',
            observation_timestamp=stamp(day,15,30).isoformat(),observation_timestamp_basis='CONCEPTUAL_SESSION_CLOSE;ORIGINAL_BUCKET_START_RETAINED',
            original_provider_timestamp=c.start_ist.isoformat(),available_at=stamp(day+timedelta(days=1),0).isoformat(),
            availability_basis='CONSERVATIVE_PROVIDER_BUCKET_COMPLETION_NEXT_MIDNIGHT;ACTUAL_DELIVERY_NOT_ATTESTED',
            source_url=SOURCES['NIFTY50'],source_file='',source_sha256='',vintage='RETAINED_UPSTOX_DAILY',
            adjustment='PRICE_INDEX;PROVIDER_ADJUSTMENT_BASIS_UNATTESTED',missing_data_flag='',timestamp_confidence='CONSERVATIVE_BUCKET_BOUND'))
    # Original Nifty source is identified independently in the retained inventory.
    inventory=json.loads((BASE/'er03/market-data-inventory.json').read_text())
    source=next(x['source'] for x in inventory['datasets'] if x['instrument_key']==nkey and x['unit']=='days')
    for r in groups['NIFTY50']:
        r.update(source_file=source['original_path'],source_sha256=source['sha256'])
    changes(groups['NIFTY50'])
    public={str((OUT/'raw'/r['file']).relative_to(ROOT)):r['received_at'] for r in ledger['requests'] if r.get('success')}
    for rows in list(groups.values())+[retrospective]:
        for r in rows:
            r['retrieved_at']=public.get(r['source_file'],'REUSED_RETAINED_ER03_INVENTORY;SEE_ORIGINAL_RECEIPT')
            r['observation_timestamp_ist']=datetime.fromisoformat(r['observation_timestamp']).astimezone(IST).isoformat() if r['observation_timestamp'] else ''
            r['available_at_ist']=datetime.fromisoformat(r['available_at']).astimezone(IST).isoformat() if r['available_at'] else ''
    return groups,retrospective,data,provenance

def context(groups,day,phase):
    cutoff=stamp(day,9,15) if phase=='PREOPEN' else stamp(day,15,30)
    result=[]
    for s in SERIES:
        pool=groups[s]
        eligible=[r for r in pool if r['available_at'] and datetime.fromisoformat(r['available_at'])<cutoff]
        # Indian close is an explicitly conceptual separate snapshot, not a
        # claim that the historical Upstox bucket had already been delivered.
        if phase=='BY_CLOSE' and s=='NIFTY50':
            eligible=[dict(r,available_at=stamp(day,15,30).isoformat(),availability_basis='SESSION_CLOSE_CONCEPTUAL;PROVIDER_DELIVERY_UNATTESTED',timestamp_confidence='CONCEPTUAL_MARKET_CLOSE') for r in pool if r['observation_date']==day]
        selected=max(eligible,key=lambda r:r['observation_date']) if eligible else None
        if selected and s=='NIFTY50' and phase=='PREOPEN':
            expected=[str(c.start_ist.date()) for c in groups_to_calendar if str(c.start_ist.date())<day]
            if not expected or selected['observation_date']!=expected[-1]:selected=None
        r=dict(selected) if selected else dict(series=s,observation_date='',value=None,unit='INR per USD' if s=='USDINR' else 'USD per barrel' if s in ('BRENT','WTI') else 'index points',
            observation_timestamp='',observation_timestamp_basis='',available_at='',availability_basis='',source_url=SOURCES[s],source_file='',source_sha256='',
            vintage='',adjustment='',missing_data_flag=MISSING[s],timestamp_confidence='UNAVAILABLE',direction_1s='UNAVAILABLE',
            **{f'change_{n}s_pct':None for n in (1,5,20)},**{f'base_{n}s_date':'' for n in (1,5,20)},horizon_basis='UNAVAILABLE')
        us=previous_us_session(day)
        r.update(indian_session=day,phase=phase,checkpoint_ist=cutoff.isoformat(),
            observation_timestamp_ist=datetime.fromisoformat(r['observation_timestamp']).astimezone(IST).isoformat() if r['observation_timestamp'] else '',
            available_at_ist=datetime.fromisoformat(r['available_at']).astimezone(IST).isoformat() if r['available_at'] else '',
            age_calendar_days=(date.fromisoformat(day)-date.fromisoformat(r['observation_date'])).days if r['observation_date'] else None,
            preopen_overnight_change_pct=None,overnight_missing_reason='NO_TIMESTAMPED_PREOPEN_AND_PRIOR_CLOSE_QUOTES',
            expected_latest_us_session=str(us.date()) if us else '',expected_us_close_ist=us.astimezone(IST).isoformat() if us else '',
            us_calendar_basis='NYSE_2026_OFFICIAL_HOLIDAYS;CALENDAR_ONLY_NOT_RETRIEVED_INDEX_DATA')
        result.append(r)
    return result

groups_to_calendar=()

def compare(checkpoints,returns):
    """Natural sign groups only. Separate snapshot, checkpoint and horizon."""
    features={(r['event_id'],int(r['checkpoint'])):r for r in read_csv(ER04/'early-checkpoint-features.csv')}
    outcomes={(r['event_id'],int(r['checkpoint']),int(r['horizon'])):r for r in returns}
    result=[]; cases=[]
    for phase in ('PREOPEN','BY_CLOSE'):
      for cp in (1,3,5):
       for horizon in (5,10,20,40):
        for s in SERIES:
         for n in (1,5,20):
          for signal in ('ALL','RS_PRICE_VOLUME_CONSTRUCTIVE'):
           if phase=='PREOPEN' and signal!='ALL':continue
           for sign in ('POSITIVE','NEGATIVE','ZERO','UNAVAILABLE'):
            selected=[]
            for r in checkpoints:
                if r['phase']!=phase or r['checkpoint']!=cp or r['series']!=s:continue
                v=r[f'change_{n}s_pct'];category='UNAVAILABLE' if v is None else 'POSITIVE' if v>0 else 'NEGATIVE' if v<0 else 'ZERO'
                if category!=sign:continue
                f=features[r['event_id'],cp]
                constructive=f['above_ema10']=='True' and f['above_ema20']=='True' and bool(f['rs55_change_from_pre_results']) and D(f['rs55_change_from_pre_results'])>0 and f['up_close']=='True' and bool(f['dss_pct']) and D(f['dss_pct'])>100
                if signal!='ALL' and not constructive:continue
                o=outcomes[r['event_id'],cp,horizon]
                selected.append((r,f,o))
            full=[t for t in selected if t[2]['return_pct'] and t[2]['relative_return_pp']]
            rel=[D(t[2]['relative_return_pp']) for t in full];absolute=[D(t[2]['return_pct']) for t in full]
            rallies=[t for t in full if D(t[1]['post_results_return_pct'])>0]
            result.append(dict(phase=phase,checkpoint=cp,horizon=horizon,series=s,macro_horizon=n,macro_change_sign=sign,stock_signal=signal,
                events=len(selected),complete_outcomes=len(full),positive_absolute_returns=sum(v>0 for v in absolute),negative_absolute_returns=sum(v<0 for v in absolute),
                outperformers=sum(v>0 for v in rel),underperformers=sum(v<0 for v in rel),
                median_return_pct=median(absolute) if absolute else None,median_relative_return_pp=median(rel) if rel else None,
                early_rally_events=len(rallies),rally_continuations=sum(D(o['return_pct'])>0 for _,_,o in rallies),rally_fades=sum(D(o['return_pct'])<=0 for _,_,o in rallies),
                unknown_next_results_boundaries=sum(t[2]['censored']=='True' for t in full),
                interpretation='EXPLORATORY_ASSOCIATION;NESTED_GROUPS;NO_CAUSALITY;UNAVAILABLE_ROWS_ARE_COVERAGE_ONLY',
                subsector_strength='UNAVAILABLE_POINT_IN_TIME_MEMBERSHIP',business_favorability='UNAVAILABLE_EVENT_TIME_NET_EXPOSURE'))
            if signal=='ALL' and n==1 and horizon==20 and cp==3 and phase=='PREOPEN' and sign!='UNAVAILABLE':
                for r,f,o in full:
                    cases.append(dict(event_id=r['event_id'],series=s,macro_sign=sign,macro_value=r['value'],macro_date=r['observation_date'],
                        macro_change_1s_pct=r['change_1s_pct'],checkpoint_ist=r['checkpoint_ist'],macro_available_at=r['available_at'],stock_signal_constructive=(f['above_ema10']=='True' and f['above_ema20']=='True' and D(f['rs55_change_from_pre_results'])>0),
                        early_return_pct=f['post_results_return_pct'],forward20_return_pct=o['return_pct'],forward20_relative_return_pp=o['relative_return_pp'],
                        unknown_boundary=o['censored'],example_type=('STRENGTH_DESPITE_WEAK_INDIAN_MARKET' if s=='NIFTY50' and sign=='NEGATIVE' and D(o['relative_return_pp'])>0 else 'SUPPORTIVE_MARKET_BUT_UNDERPERFORMED' if s=='NIFTY50' and sign=='POSITIVE' and D(o['relative_return_pp'])<0 else 'OTHER_DESCRIPTIVE_CASE')))
    return result,cases

def future_descriptions(daily,features,returns):
    """Future observations live in a separate file, never earlier features."""
    result=[]
    for f in features:
      for o in returns:
        if (f['event_id'],f['checkpoint'])!=(o['event_id'],o['checkpoint']):continue
        for s in ('BRENT','WTI','USDINR','NIFTY50'):
            pool=sorted([r for r in daily if r['series']==s and r['value'] is not None and (s=='NIFTY50' or r['vintage']=='CURRENT_AT_RETRIEVAL')],key=lambda r:r['observation_date'])
            start=next((r for r in reversed(pool) if r['observation_date']<=f['checkpoint_date']),None)
            end=next((r for r in reversed(pool) if r['observation_date']<=o['exit_date']),None)
            usable=start and end and start['observation_date']!=end['observation_date'] and end['observation_date']>=o['entry_date']
            result.append(dict(event_id=f['event_id'],checkpoint=f['checkpoint'],horizon=o['horizon'],series=s,
                start_observation_date=start['observation_date'] if start else '',end_observation_date=end['observation_date'] if end else '',
                start_value=start['value'] if usable else None,end_value=end['value'] if usable else None,
                retrospective_change_pct=100*(end['value']/start['value']-1) if usable else None,
                role='AFTER_CHECKPOINT_OUTCOME_DESCRIPTION_ONLY;NEVER_PREDICTIVE_INPUT',
                missing_data_flag='' if usable else 'INSUFFICIENT_FORWARD_COVERAGE',source_file=end['source_file'] if end else '',
                endpoint_alignment='LAST_OBSERVATION_ON_OR_BEFORE_TARGET;SEE_ACTUAL_DATES;NOT_EXACT_CLOSE_QUOTE'))
    return result

def inventory(groups,retrospective):
    result=[]
    coverage={'SP500':'FRED rolling ten years','DJIA':'FRED rolling ten years','NASDAQCOM':'FRED from 1971',
      'ES':'CME historical tick data; specific entitlement and requested dates must be confirmed','NQ':'CME historical tick data; specific entitlement and requested dates must be confirmed',
      'BRENT':'FRED/EIA from 1987','WTI':'FRED/EIA from 1986','USDINR':'Federal Reserve daily country series; dated H10 archives available since 2009',
      'NIFTY50':'Retained 2025-07-01 to 2026-08-14','INDIA_VIX':'Official NSE historical interface; start date not confirmed here',
      'GIFT_NIFTY':'NSE IX era from July 2023; actual intraday archive coverage unconfirmed'}
    for s in SERIES:
        rows=groups[s];retro=[r for r in retrospective if r['series']==s and r['value'] is not None]
        result.append(dict(series=s,source_url=SOURCES[s],existing_repository_rows=len(rows) if s=='NIFTY50' else 0,
            recovered_timestamp_eligible_rows=len(rows),recovered_retrospective_rows=len(retro),
            first_observation=min([r['observation_date'] for r in rows+retro],default=''),last_observation=max([r['observation_date'] for r in rows+retro],default=''),
            historical_coverage=coverage[s],status='RECOVERED' if rows or retro else 'UNAVAILABLE',
            missing_data_flag=rows[0]['missing_data_flag'] if rows else MISSING[s],
            timestamp_convention=rows[0]['availability_basis'] if rows else 'NO_VERIFIED_HISTORICAL_PUBLICATION_TIME',
            adjustment_convention=rows[0]['adjustment'] if rows else retro[0]['adjustment'] if retro else 'NOT_ACQUIRED;CONTRACT_SPECIFIC_UNADJUSTED_REQUIRED_FOR_FUTURES',
            access_restrictions='COPYRIGHTED_PREAPPROVAL_REQUIRED;NOT_DOWNLOADED_OR_REDISTRIBUTED' if s in ('SP500','DJIA','NASDAQCOM') else 'LICENSED_TIMESTAMPED_DATA_NOT_ACQUIRED' if s in ('ES','NQ','GIFT_NIFTY') else 'PUBLIC_DOMAIN_CITATION_REQUESTED' if s in ('BRENT','WTI','USDINR') else 'RETAINED_OWNER_AUTHORIZED_UPSTOX' if s=='NIFTY50' else 'PUBLIC_INTERFACE_TRANSPORT_FAILED;NO_COOKIE_OR_AUTH_BYPASS',
            units='INR per USD' if s=='USDINR' else 'USD per barrel' if s in ('BRENT','WTI') else 'annualized volatility percent' if s=='INDIA_VIX' else 'index points'))
    return result

def chart_examples(preopen,byclose):
    """Copy ER04 examples; extend selected-date inspection in copied assets only."""
    dest=OUT/'charts';dest.mkdir(exist_ok=True)
    shutil.copytree(ER04/'charts/assets',dest/'assets',dirs_exist_ok=True)
    js=dest/'assets/chart.js'
    old=js.read_text();hook="    document.getElementById('inspection').textContent="
    assert old.count(hook)==1
    old=old.replace(hook,"    if(window.renderMarketContext)window.renderMarketContext(r.date);\n"+hook)
    js.write_text(old)
    packed={}
    for r in preopen+byclose:
        day=r['indian_session'];packed.setdefault(day,[]).append({k:r.get(k) for k in ('phase','series','value','unit','observation_date','observation_timestamp','available_at','observation_timestamp_ist','available_at_ist','change_1s_pct','change_5s_pct','change_20s_pct','missing_data_flag','timestamp_confidence')})
    script='''
    (()=>{const byDay=JSON.parse(document.getElementById('macro-data').textContent);
    const f=v=>v===null||v===undefined?'UNAVAILABLE':typeof v==='number'?v.toFixed(3):String(v);
    window.renderMarketContext=day=>{const panel=document.getElementById('market-context');
    const rows=byDay[day]||[];panel.replaceChildren();const title=document.createElement('summary');
    title.textContent=day+' · Market context · pre-open 09:15 IST / close 15:30 IST';panel.append(title);
    const note=document.createElement('p');note.textContent='Missing oil/US/futures timestamps remain unavailable. USD/INR uses the latest H.10 release proven available by a conservative release-date bound; actual publication minute and live FX are unavailable. Available-at is a bound, not an actual receipt. Close Nifty is conceptual. Full chart includes future bars.';panel.append(note);
    const table=document.createElement('table');table.style.width='100%';table.style.fontSize='11px';
    const head=document.createElement('tr');for(const x of ['Phase / series','Value / unit','Observed (IST)','Availability bound (IST)','1 / 5 / 20 sessions (%)','Status']){const t=document.createElement('th');t.textContent=x;head.append(t)}table.append(head);
    for(const r of rows){const tr=document.createElement('tr');for(const x of [r.phase+' / '+r.series,f(r.value)+' '+r.unit,r.observation_timestamp_ist||r.observation_date||'UNAVAILABLE',r.available_at_ist||'UNAVAILABLE',[r.change_1s_pct,r.change_5s_pct,r.change_20s_pct].map(f).join(' / '),r.missing_data_flag||r.timestamp_confidence]){const td=document.createElement('td');td.textContent=x;tr.append(td)}table.append(tr)}panel.append(table);panel.dataset.selectedDate=day;};})();
    '''
    names=[]
    for symbol in ('TIPSMUSIC','CEATLTD','AETHER','TARIL','SHILCTECH'):
        name='FY2027-Q1-'+symbol+'-S3.html';text=(ER04/'charts'/name).read_text()
        panel='<details id="market-context" open style="padding:8px 14px;border-bottom:1px solid #334154"></details>'
        text=text.replace('<main>',panel+'<main>')
        insert='<script id="macro-data" type="application/json">'+json.dumps(packed,default=lambda x:float(x) if isinstance(x,D) else str(x),separators=(',',':'))+'</script><script>'+script+'</script>'
        text=text.replace('<script src="assets/chart.js"></script>',insert+'<script src="assets/chart.js"></script>')
        (dest/name).write_text(text);names.append(name)
    (dest/'index.html').write_text('<!doctype html><meta charset="utf-8"><h1>ER04 market context examples</h1>'+''.join(f'<p><a href="{n}">{n}</a></p>' for n in names))
    write_json(OUT/'chart-index.json',dict(charts=names,renderer='Existing ER03C four panels; selected-date context inspection added in copied assets'))
    return names

def outstanding(features):
    """Exact executable requests only where provider instrument identity is known.

    Futures/US licensing requirements have a precise data selection specification,
    but no invented provider credentials, SKU, API URL or contractual entitlement.
    """
    import gzip
    from .retrieve_er03 import DailyRequest
    from .upstox_candles import url_for
    metadata=BASE/'ta02b/public-nse-instruments.json.gz'
    master=json.loads(gzip.decompress(metadata.read_bytes()))
    requests=[]
    for symbol,identifier,start,end in [('NIFTY50','Nifty 50','2026-08-15','2026-10-30'),('INDIA_VIX','India VIX','2026-02-01','2026-10-30')]:
        found=[r for r in master if r.get('instrument_key')=='NSE_INDEX|'+identifier]
        if len(found)!=1:raise ValueError('Index identity unavailable')
        q=DailyRequest(found[0]['instrument_key'],'days',1,date.fromisoformat(start),date.fromisoformat(end))
        requests.append(dict(number=len(requests)+1,symbol=symbol,instrument_key=q.instrument_key,unit='days',interval=1,from_date=start,to_date=end,url=url_for(q)))
    # No repetition of the exhausted SHILCTECH request: provider identity/history
    # diagnosis is required before proposing another instrument or interval.
    write_json(OUT/'OUTSTANDING_ACQUISITION_MANIFEST.json',dict(status='NOT_EXECUTED_NEW_AUTHENTICATED_REQUESTS_REQUIRE_OWNER_AUTHORIZATION',
        request_count=len(requests),requests=requests,metadata_path=str(metadata.relative_to(ROOT)),metadata_sha256=digest(metadata),
        automatic_retries=0,scope='ER04 existing 24 events only; Nifty gap and India VIX daily context',
        execution_policy='Serial >=1s; validate identities and full responses; stop on first HTTP/schema/empty/transport failure; no substitution',
        global_data_requirements=dict(status='LICENSE_AND_PROVIDER_SELECTION_REQUIRED;NOT_AN_EXECUTABLE_HTTP_MANIFEST',
            equity_series=['SP500','NASDAQCOM','DJIA'],equity_date_range=['2026-03-01','2026-10-30'],
            equity_fields=['session_date','unadjusted_price_index_close','actual_close_timestamp','publication_timestamp','revision_vintage'],
            futures_series=['CME ES','CME NQ','NSE IX GIFT NIFTY'],
            indian_checkpoint_dates=sorted({f['checkpoint_date'] for f in features}),
            preopen_cutoff_ist='09:15:00 EXCLUSIVE',required_fields=['exchange_contract_id','expiry','trade_or_quote_timestamp_with_timezone','publication_timestamp','price','currency','previous_comparable_session_close_and_timestamp','roll_policy'],
            request_policy='Latest actual observation before cutoff; no EOD settlements or continuous back-adjusted substitutions; enumerate exact vendor requests only after entitlement confirmed'),
        shilctech='Original exact request exhausted: only five rows from 2025-11-24, its NSE listing date verified in issuer May-2026 results note 5. No earlier NSE history exists. BSE mixing would require a separately reviewed methodology, instrument identity, adjustments and authorization; not performed.'))
    (OUT/'OUTSTANDING_ACQUISITION_MANIFEST.sha256').write_text(digest(OUT/'OUTSTANDING_ACQUISITION_MANIFEST.json')+'\n')

def main():
    OUT.mkdir(exist_ok=True)
    groups,retro,data,provenance=observations()
    global groups_to_calendar
    groups_to_calendar=data[BENCH]
    sessions=sorted({str(c.start_ist.date()) for rows in data.values() for c in rows})
    daily=retro+[r for rows in groups.values() for r in rows]
    # Uniform CSV schemas even where provider-only fields are missing.
    def write(name,rows):
        fields=list(dict.fromkeys(k for r in rows for k in r))
        write_csv(OUT/name,[{k:r.get(k) for k in fields} for r in rows])
    write('GLOBAL_MARKET_DATA_INVENTORY.csv',inventory(groups,retro))
    write('MACRO_DAILY_CONTEXT.csv',daily)
    pre=[r for day in sessions for r in context(groups,day,'PREOPEN')]
    close=[r for day in sessions for r in context(groups,day,'BY_CLOSE')]
    write('PREOPEN_CONTEXT.csv',pre)
    write('BY_CLOSE_CONTEXT.csv',close)
    fs=read_csv(ER04/'early-checkpoint-features.csv');returns=read_csv(ER04/'forward-returns-drawdowns.csv')
    indexed={(r['indian_session'],r['phase'],r['series']):r for r in pre+close}
    cps=[]
    for f in fs:
        for phase in ('PREOPEN','BY_CLOSE'):
            for s in SERIES:
                r=dict(indexed[f['checkpoint_date'],phase,s],event_id=f['event_id'],symbol=f['symbol'],checkpoint=int(f['checkpoint']),subsector=f['subsector'])
                # End-of-session stock indicators are NEVER pre-open inputs.
                r.update(stock_features_available=phase=='BY_CLOSE',stock_feature_reference='../early-checkpoint-features.csv' if phase=='BY_CLOSE' else '',
                    stock_feature_limit='SESSION_CLOSE_CONCEPTUAL;NO_PREOPEN_USE' if phase=='BY_CLOSE' else 'SAME_DAY_CLOSE_FEATURES_EXCLUDED',
                    subsector_strength=None,subsector_missing_reason='HISTORICAL_MEMBERSHIP_NOT_ATTESTED',business_favorability=None,business_missing_reason='NO_EVENT_TIME_NET_EXPOSURE_CLASSIFICATION')
                cps.append(r)
    write('EARNINGS_MACRO_CHECKPOINTS.csv',cps)
    comparison,cases=compare(cps,returns)
    write('MACRO_CONTINUATION_COMPARISON.csv',comparison)
    write('MACRO_COUNTEREXAMPLES.csv',cases)
    write('AFTER_CHECKPOINT_MACRO_OUTCOMES.csv',future_descriptions(daily,fs,returns))
    # Correct-time quote series are absent, so even nonzero daily values cannot
    # leak into their checkpoint cells.
    assert len(cps)==72*2*11
    assert all(r['value'] is None for r in cps if r['series'] in ('ES','NQ','BRENT','WTI','GIFT_NIFTY','SP500','NASDAQCOM','DJIA'))
    assert all(not r['stock_features_available'] for r in cps if r['phase']=='PREOPEN')
    assert all(not r['available_at'] or datetime.fromisoformat(r['available_at'])<=datetime.fromisoformat(r['checkpoint_ist']) for r in cps)
    write_json(OUT/'context-validation.json',dict(earnings_events=24,checkpoints=72,joined_rows=len(cps),series=11,
        nonmissing_by_series_phase={s+':'+p:sum(r['value'] is not None for r in cps if r['series']==s and r['phase']==p) for s in SERIES for p in ('PREOPEN','BY_CLOSE')},
        historical_release_rows=len(groups['USDINR']),no_future_publication_leak=True,no_preopen_stock_close_features=True,
        no_settlement_futures_substitutions=True,current_vintage_oil_fx_excluded_from_inputs=True,
        assumption='H10 actual publication minute unavailable: safe upper bound is end of dated NY release day; official scheduled 16:15 ET is retained separately and never used as actual timestamp',
        comparison_limit='RS_PRICE_VOLUME_CONSTRUCTIVE groups exist only for BY_CLOSE decisions. PREOPEN comparisons never filter on same-day closing stock signals.'))
    names=chart_examples(pre,close)
    outstanding(fs)
    if '--compact-review' in __import__('sys').argv:
        baseline=json.loads((COMPLETION/'preservation-before.json').read_text())
        present={p:h for p,h in baseline.items() if (ROOT/p).is_file()}
        changed=[p for p,h in present.items() if digest(ROOT/p)!=h]
        write_json(OUT/'compact-preservation-verification.json',dict(included_files=len(present),omitted_original_baseline_files=len(baseline)-len(present),
            changed=changed,included_files_passed=not changed,full_baseline_verification=False))
        if changed:raise ValueError('Included original evidence changed')
    else:
        preservation()
    drift=json.loads((COMPLETION/'preexisting-preservation-drift.json').read_text())
    for f in drift['files']:assert digest(ROOT/f['path'])==f['task_start_sha256']
    write_json(OUT/'six-preview-discrepancies-preserved.json',dict(files=drift['files'],task_start_bytes_unchanged=True,discrepancies_repaired=False))
    write_json(OUT/'status.json',dict(status='BOUNDED_EXTENSION_COMPLETE_WITH_EXPLICIT_DATA_GAPS',phase_a_requests_executed=11,
        phase_a_complete_response_count=10,classified_stock_checkpoints=66,total_stock_checkpoints=72,
        macro_daily_recovered=['BRENT_SPOT','WTI_SPOT','USDINR_H10','NIFTY50_REUSED'],
        timestamp_eligible_inputs=['USDINR_DATED_WEEKLY_RELEASE','NIFTY50_RETAINED_RANGE'],
        us_equities='NOT_ACQUIRED_LICENSE_RESTRICTIONS',futures='UNAVAILABLE_NO_TIMESTAMPED_HISTORICAL_PREOPEN_QUOTES',
        annotated_chart_examples=len(names),stop_after_er04=True))
    print((OUT/'context-validation.json').read_text())

if __name__=='__main__':main()
