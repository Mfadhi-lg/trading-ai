"""ER-03 offline historical pilot. No network, secret access or production writes.
Run: python3 -m experiments.earnings_reaction_v1.er03
"""
from __future__ import annotations
import csv
import gzip
import hashlib
import html
import json
import sqlite3
from statistics import median
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
from .candles import Candle
from .indicators import pine_average, pine_rvol, _dtr_arithmetic, calculate_chart_rs

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT/'artifacts/earnings-reaction-v1'
OUT = BASE/'er03'
DB = ROOT/'artifacts/acquisition-readiness/AR-01.3C/attempt-02-post-canary/scraper.db'
BENCH = 'NSE_INDEX|NIFTY MIDSML 400'
CUTOFF = '2026-10-30'
IST = ZoneInfo('Asia/Kolkata')
# Fixed by business relevance and data availability, never by realized returns.
PILOT = ((609,'AETHER'),(173,'TIPSMUSIC'),(68,'CEATLTD'),(273,'GRPLTD'),
         (487,'SYRMA'),(610,'VOLTAMP'),(1240,'CENTUM'),(1864,'MRF'),
         (2134,'SHILCTECH'),(2267,'TOLINS'),(110,'TARIL'),(1695,'PGEL'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, default=str, indent=2, sort_keys=True)+'\n')


def write_csv(path, rows):
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)


def read_csv(path):
    with path.open() as f:
        return list(csv.DictReader(f))


def candle(key, row):
    t = datetime.fromisoformat(row[0]); end = t+timedelta(days=1)
    o,h,l,c,v,oi = map(lambda x: Decimal(str(x)), row[1:])
    if min(o,h,l,c)<=0 or not l<=min(o,c)<=max(o,c)<=h or v<0:
        raise ValueError('invalid OHLCV')
    return Candle(key,row[0],t,end,o,h,l,c,v,oi,'provider_daily_bucket',end,None)


def features(stock, benchmark):
    """Never drop a stock session to manufacture RS alignment.

    With a missing benchmark session, RS resumes only after a new complete
    aligned 56-observation stock sequence. Other features retain all stock bars.
    """
    stock=tuple(stock)
    tr,atr,rv20,_ = _dtr_arithmetic(stock)
    rv21,_ = pine_rvol((s.volume for s in stock),21)
    ema10=pine_average((s.close for s in stock),10,'EMA')
    ema20=pine_average((s.close for s in stock),20,'EMA')
    rs=[None]*len(stock); run=[]; indices=[]
    cutoff=datetime(2026,11,1,tzinfo=IST)
    def finish():
        if run:
            result=calculate_chart_rs(run,[benchmark[s.start_ist.date().isoformat()] for s in run],
                                     cutoff=cutoff,timeframe='Daily',rs_length=55)
            for i,r in zip(indices,result):rs[i]=r['rs']
    for i,s in enumerate(stock):
        if s.start_ist.date().isoformat() not in benchmark:
            finish();run=[];indices=[]
        else:
            run.append(s);indices.append(i)
    finish()
    return [dict(date=str(s.start_ist.date()),open=s.open,high=s.high,low=s.low,close=s.close,
                 volume=s.volume,ema10=ema10[i],ema20=ema20[i],true_range=tr[i],
                 atr14=atr[i],daily_range=s.high-s.low,rvol20=rv20[i],rvol21=rv21[i],rs55=rs[i],
                 rs55_status='AVAILABLE' if rs[i] is not None else 'WARMUP_OR_MISSING_EXACT_BENCHMARK',
                 available_after=str(s.completed_at)) for i,s in enumerate(stock)]


def window_rows(rows, start_date, next_date, horizon=None):
    eligible=[r for r in rows if r['date']>start_date and (not next_date or r['date']<next_date)]
    return eligible[:horizon] if horizon else eligible


def performance(rows):
    """Returns from first post-results session OPEN; no announcement-day entry.

    Max rise/min low relative to that open; maximum drawdown uses running
    closing-price peaks including the entry open, not ambiguous intraday order.
    """
    base=rows[0]['open']; peak=base; drawdown=Decimal(0)
    for r in rows:
        peak=max(peak,r['close']);drawdown=min(drawdown,100*(r['close']/peak-1))
    return dict(entry_open=base,exit_close=rows[-1]['close'],return_pct=100*(rows[-1]['close']/base-1),
                maximum_rise_pct=100*(max(r['high'] for r in rows)/base-1),
                minimum_low_return_pct=100*(min(r['low'] for r in rows)/base-1),
                maximum_close_drawdown_pct=drawdown,
                above_ema20_sessions=sum(r['close']>r['ema20'] for r in rows),
                rvol20_ge2_sessions=sum(r['rvol20'] is not None and r['rvol20']>=2 for r in rows),
                up_close_rvol20_ge2_sessions=sum(i>0 and r['close']>rows[i-1]['close'] and
                    r['rvol20'] is not None and r['rvol20']>=2 for i,r in enumerate(rows)),
                down_close_rvol20_ge2_sessions=sum(i>0 and r['close']<rows[i-1]['close'] and
                    r['rvol20'] is not None and r['rvol20']>=2 for i,r in enumerate(rows)),
                rs55_first=rows[0]['rs55'],rs55_last=rows[-1]['rs55'],
                rs55_positive_sessions=sum(r['rs55'] is not None and r['rs55']>0 for r in rows),
                rs55_available_sessions=sum(r['rs55'] is not None for r in rows))


def chart(path, symbol, quarter, rows):
    """Standalone labeled SVG, actual OHLC, volume, EMAs and RS; no libraries."""
    n=len(rows); x=lambda i:65+(i+.5)*890/n
    parts=['<svg xmlns="http://www.w3.org/2000/svg" width="1040" height="820" viewBox="0 0 1040 820">',
           '<rect width="1040" height="820" fill="white"/>',
           f'<text x="45" y="26" font-size="19">{symbol} · {quarter} · APPROXIMATE post-results window</text>',
           '<text x="45" y="49" font-size="12">Upstox retained daily candles · price INR · volume provider units · source-derived indicators</text>']
    def text(x,y,t,size=12):parts.append(f'<text x="{x}" y="{y}" font-size="{size}">{html.escape(str(t))}</text>')
    def panel(keys, top, height, title, colors):
        vals=[float(r[k]) for r in rows for k in keys if r[k] is not None]
        if not vals:return
        lo,hi=min(vals),max(vals);span=hi-lo or 1
        y=lambda v:top+height-(float(v)-lo)/span*height
        text(65,top-12,title)
        for v in [lo,(lo+hi)/2,hi]:
            yy=y(v); parts.append(f'<path d="M65 {yy}H955" stroke="#ddd"/>');text(5,yy+4,f'{v:.2f}',10)
        if keys[0]=='low':
            w=max(1,min(7,600/n))
            for i,r in enumerate(rows):
                color='#137969' if r['close']>=r['open'] else '#b13f45'
                parts.append(f'<path d="M{x(i)} {y(r["low"])}V{y(r["high"])}" stroke="{color}"/>')
                parts.append(f'<rect x="{x(i)-w/2}" y="{min(y(r["open"]),y(r["close"]))}" width="{w}" height="{max(1,abs(y(r["close"])-y(r["open"])))}" fill="{color}"/>')
        for k,color in zip(keys,colors):
            if k in ('low','high'):continue
            pts=' '.join(f'{x(i)},{y(r[k])}' for i,r in enumerate(rows) if r[k] is not None)
            parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.5"/>')
    panel(['low','high','ema10','ema20'],90,235,'Price (INR) · candles; EMA10 blue; EMA20 orange',['','','#2463aa','#bb7418'])
    panel(['volume'],385,105,'Volume (provider units)',['#667788'])
    panel(['rvol20','rvol21'],550,80,'Relative volume (ratio) · RVOL20 blue; RVOL21 orange',['#2463aa','#bb7418'])
    panel(['rs55'],690,65,'RS55 vs NIFTYMIDSML400 (dimensionless) · exact alignment; blanks unavailable',['#2463aa'])
    for i in sorted({0,n//2,n-1}):text(x(i)-30,780,rows[i]['date'],10)
    text(460,805,'Date (observed provider sessions)',12)
    parts.append('</svg>');path.write_text('\n'.join(parts))


def subsector_index(series):
    members=sorted(series)
    if len(members)<3:return [],dict(status='INSUFFICIENT_PEERS',members=members)
    first=max(rows[0]['date'] for rows in series.values())
    last=min(rows[-1]['date'] for rows in series.values())
    subsets={s:[r for r in series[s] if first<=r['date']<=last] for s in members}
    dates=[r['date'] for r in subsets[members[0]]]
    if not dates or any([r['date'] for r in subsets[s]]!=dates for s in members):
        return [],dict(status='MISSING_EXACT_PEER_ALIGNMENT',members=members,first=first,last=last)
    rows=[dict(date=day,index_close=100*sum((subsets[s][i]['close']/subsets[s][0]['close'] for s in members),Decimal(0))/len(members)) for i,day in enumerate(dates)]
    return rows,dict(status='CALCULATED',members=members,first=first,last=last,rows=len(rows),base=100,weights='initial 1/N; fixed holdings, drifting weights',calendar='provider observed, exact member alignment')


def combined_indices_chart(path, source):
    data={p.stem:read_csv(p) for p in source.glob('*-index.csv')}
    if not data:return
    all_values=[float(r['index_close']) for rows in data.values() for r in rows]
    lo,hi=min(all_values),max(all_values);span=hi-lo or 1
    parts=['<svg xmlns="http://www.w3.org/2000/svg" width="1040" height="450">',
           '<rect width="1040" height="450" fill="white"/>',
           '<text x="60" y="28" font-size="18">Pilot subsector closing-price indices · fixed initial equal weights</text>',
           '<text x="60" y="50" font-size="12">Source: retained Upstox daily closes · 2025-12-01 to 2026-10-30 · base 100 · retrospective pilot subsets</text>']
    y=lambda value:365-(value-lo)/span*260
    for value in [lo,(lo+hi)/2,hi]:
        parts.append(f'<path d="M65 {y(value)}H945" stroke="#ddd"/><text x="5" y="{y(value)}" font-size="11">{value:.1f}</text>')
    for n,(name,rows) in enumerate(sorted(data.items())):
        color=['#2463aa','#bb7418','#137969'][n]
        pts=' '.join(f'{65+i*880/(len(rows)-1)},{y(float(r["index_close"]))}' for i,r in enumerate(rows))
        parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2"/>')
        parts.append(f'<text x="{65+n*285}" y="82" fill="{color}" font-size="12">{name.replace("-index","")}</text>')
    parts += ['<text x="65" y="390" font-size="12">2025-12-01</text>',
              '<text x="880" y="390" font-size="12">2026-10-30</text>',
              '<text x="460" y="415" font-size="12">Date · Index points (base 100)</text>',
              '<text x="65" y="440" font-size="11">Not tutor TradingView candles or volume profiles; provider adjustment basis unknown.</text></svg>']
    path.parent.mkdir(exist_ok=True);path.write_text('\n'.join(parts))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    # Preserve all bytes under both earlier event-isolation studies, including evidence.
    old=BASE/'event-isolation'
    preserved={str(p.relative_to(ROOT)):digest(p) for p in old.rglob('*') if p.is_file()}
    write_json(OUT/'preservation-before.json',preserved)
    inputs=json.loads((old/'inputs.json').read_text())
    metadata_path=BASE/'ta02b/public-nse-instruments.json.gz'
    meta=json.loads(gzip.decompress(metadata_path.read_bytes()))
    bysymbol={r.get('trading_symbol'):r for r in meta if r.get('segment')=='NSE_EQ'}
    classes={int(r['company_id']):r for r in read_csv(ROOT/'artifacts/subsector-ss04/COMPANY_SUBSECTOR_FINAL.csv')}
    context_path=ROOT/'artifacts/b2-discovery-2026-10-01/B2_DECISION_UNIVERSE.csv'
    pilot_ids={str(cid) for cid,_ in PILOT}
    context=[r for r in read_csv(context_path) if r['company_id'] in pilot_ids]
    write_csv(OUT/'existing-financial-research-context.csv',context)
    labels=json.loads((ROOT/'artifacts/focus_ml/governance/FOCUS_LABEL_REGISTRY_v001.json').read_text())['rows']
    # No zero labels in the retained authoritative registry.
    assert not any(r.get('label')==0 for r in labels)
    db=sqlite3.connect(f'file:{DB}?mode=ro',uri=True);db.row_factory=sqlite3.Row
    symbols=tuple(s for _,s in PILOT)
    placeholders=','.join('?' for _ in symbols)
    filings=[dict(r) for r in db.execute(f'SELECT * FROM exchange_result_filings WHERE exchange_key IN ({placeholders}) AND period_end_date IN (?,?,?)',symbols+('2026-03-31','2026-06-30','2026-09-30'))]
    db.close()
    write_json(OUT/'retained-results-records.json',filings)
    source_dates={}
    for symbol in symbols:
        for period in ['2026-03-31','2026-06-30','2026-09-30']:
            candidates=[]
            for r in filings:
                raw=json.loads(r['raw_row_json'])
                if r['exchange_key']==symbol and r['period_end_date']==period and raw.get('TYPE OF SUBMISSION','').lower()=='original':
                    day=datetime.fromisoformat(r['broadcast_at_utc']).astimezone(IST).date().isoformat()
                    if day>period:candidates.append((day,r['filing_id']))
            if candidates:source_dates[symbol,period]=min(candidates)
    inventory=json.loads((old/'PRICE_SOURCE_INVENTORY.json').read_text())
    execution_path=OUT/'retrieval-execution.json'
    execution=json.loads(execution_path.read_text()) if execution_path.exists() else None
    if execution:
        for r in execution['requests']:
            if 'retained_path' not in r:continue
            inventory.append(dict(instrument_key=r['instrument_key'],unit=r['unit'],interval=r['interval'],
                requested_from=r['from_date'],requested_to=r['to_date'],rows=r['rows'],
                source=dict(original_path=r['retained_path'],sha256=r['response_sha256'])))
    write_json(OUT/'market-data-inventory.json',dict(scope='Retained ER01 sources plus owner-approved ER03 acquisitions',datasets=inventory,
        authorization_records=['artifacts/earnings-reaction-v1/er03/retrieval-execution.json',
                               'artifacts/earnings-reaction-v1/ta03r4/authorization.json'],
        status='OWNER_APPROVED_AND_EXECUTED' if execution and execution['complete'] else 'PARTIAL_OR_OFFLINE'))
    candles={}; input_hashes={str(p.relative_to(ROOT)):digest(p) for p in [metadata_path,context_path,
        ROOT/'artifacts/focus_ml/governance/FOCUS_LABEL_REGISTRY_v001.json',
        ROOT/'artifacts/subsector-ss04/COMPANY_SUBSECTOR_FINAL.csv']}
    for item in inventory:
        if item['unit']!='days':continue
        p=ROOT/item['source']['original_path'];input_hashes[str(p.relative_to(ROOT))]=digest(p)
        assert digest(p)==item['source']['sha256']
        rows=json.loads(p.read_text())['data']['candles']
        series=tuple(sorted((candle(item['instrument_key'],r) for r in rows),key=lambda c:c.start_ist))
        assert len({c.start_ist for c in series})==len(series)
        prior={c.start_ist:c for c in candles.get(item['instrument_key'],())}
        for c in series:
            if c.start_ist in prior and c!=prior[c.start_ist]:raise ValueError('conflicting retained candles')
            prior[c.start_ist]=c
        candles[item['instrument_key']]=tuple(prior[t] for t in sorted(prior))
    bm={str(c.start_ist.date()):c for c in candles[BENCH]}
    all_features={}; quality=[]
    for cid,symbol in PILOT:
        key=bysymbol[symbol]['instrument_key']
        if key not in candles:continue
        rows=features(candles[key],bm);all_features[symbol]=rows
        write_csv(OUT/'daily'/f'{symbol}.csv',rows)
        for r in rows:
            if date.fromisoformat(r['date']).weekday()>=5:
                quality.append(dict(symbol=symbol,date=r['date'],issue='DOCUMENTED_SPECIAL_SESSION_20260201' if r['date']=='2026-02-01' else 'WEEKEND_PROVIDER_BAR_SESSION_STATUS_UNVERIFIED',action='RETAINED; existing indicators.SPECIAL_SESSION_SOURCE documents February 1; complete calendar still unverified'))
    write_csv(OUT/'market-data-quality-flags.csv',quality)
    coverage=[]
    for cid,symbol in PILOT:
        rows=all_features.get(symbol,[])
        if not rows:continue
        observed={r['date'] for r in rows}; lo=max(rows[0]['date'],min(bm));hi=min(rows[-1]['date'],max(bm))
        missing=sorted(d for d in bm if lo<=d<=hi and d not in observed)
        extra=sorted(d for d in observed if lo<=d<=hi and d not in bm)
        coverage.append(dict(symbol=symbol,first=rows[0]['date'],last=rows[-1]['date'],rows=len(rows),
            benchmark_overlap_start=lo,benchmark_overlap_end=hi,missing_observed_benchmark_dates=json.dumps(missing),
            extra_observed_stock_dates=json.dumps(extra),status='EXACT_PROVIDER_DATE_ALIGNMENT' if not missing and not extra else 'GAPS_REQUIRE_REVIEW',
            calendar_limit='Cross-provider-series consistency only; not an independently verified exchange calendar'))
    write_csv(OUT/'daily-coverage-validation.csv',coverage)
    events=[];results=[];companies=[];requests=[]
    for cid,symbol in PILOT:
        cls=classes[cid];key=bysymbol[symbol]['instrument_key']
        companies.append(dict(company_id=cid,symbol=symbol,company_name=cls['company_name'],instrument_key=key,
                              subsector=cls['primary_code'],classification_status=cls['status'],
                              classification_known_at=cls['known_at'],historical_classification_availability=cls['historical_availability'],
                              retained_daily=key in candles,selection_reason='Existing granular peer relevance or retained daily coverage; no return-based selection'))
        if symbol!='TIPSMUSIC':
            a,b=('2026-08-15',CUTOFF) if symbol=='AETHER' else ('2025-12-01',CUTOFF)
            requests.append(dict(symbol=symbol,instrument_key=key,unit='days',interval=1,from_date=a,to_date=b))
        for quarter,period,nperiod in [('FY2026-Q4','2026-03-31','2026-06-30'),('FY2027-Q1','2026-06-30','2026-09-30')]:
            start=source_dates.get((symbol,period));nxt=source_dates.get((symbol,nperiod))
            selected=[r['label_source_id'] for r in labels if r.get('company_id')==cid and r['quarter']==quarter]
            decision='MENTOR_SELECTED_FOCUS_REFERENCE' if selected else 'UNKNOWN_NOT_REJECTED'
            for horizon in ([None] if nxt else [20,40]):
                rows=window_rows(all_features.get(symbol,[]),start[0],nxt[0] if nxt else None,horizon) if start else []
                eid=f'{quarter}:{symbol}'+(f':H{horizon}' if horizon else '')
                censored=bool(start and (not rows or (horizon and len(rows)<horizon) or (nxt and all_features.get(symbol,[]) and all_features[symbol][-1]['date']<nxt[0])))
                event=dict(event_id=eid,company_id=cid,symbol=symbol,quarter=quarter,period_end=period,
                    window_label='APPROXIMATE',reported_results_date=start[0] if start else '',
                    results_record_id=start[1] if start else '',next_results_date=nxt[0] if nxt else '',
                    next_results_record_id=nxt[1] if nxt else '',horizon_sessions=horizon or '',
                    boundary_rule='FIRST_SESSION_AFTER_RESULTS_TO_LAST_SESSION_BEFORE_NEXT' if nxt else 'FIXED_TRADING_SESSION_HORIZON_NEXT_DATE_UNKNOWN',
                    observed_first_date=rows[0]['date'] if rows else '',observed_last_date=rows[-1]['date'] if rows else '',
                    observed_sessions=len(rows),right_censored=censored,
                    stock_pre_event_bars=sum(r['date']<=start[0] for r in all_features.get(symbol,[])) if start else 0,
                    rs55_available_at_first_observation=bool(rows and rows[0]['rs55'] is not None),
                    status='UNRESOLVED_START_DATE' if not start else 'ANALYZED_RETAINED_PROVIDER_SESSIONS' if rows else 'AWAITING_AUTHORIZED_CANDLES',
                    mentor_decision=decision,label_evidence=json.dumps(selected),subsector=cls['primary_code'],
                    classification_status=cls['status'],session_calendar_status='PROVIDER_OBSERVED_SESSIONS_PROVISIONAL; gaps/special sessions unverified',
                    data_cutoff=all_features[symbol][-1]['date'] if symbol in all_features else '',
                    source_date_basis='Retained Original quarterly-results record calendar date; not proof of earliest disclosure')
                events.append(event)
                if rows:
                    results.append(dict(event_id=eid,symbol=symbol,quarter=quarter,window_label='APPROXIMATE',
                        first_date=rows[0]['date'],last_date=rows[-1]['date'],observations=len(rows),
                        right_censored=censored,mentor_decision=decision,**performance(rows)))
                    chartdir=OUT/'charts';chartdir.mkdir(exist_ok=True)
                    chart(chartdir/f'{eid.replace(":","-")}.svg',symbol,quarter,rows)
    requests.insert(0,dict(symbol='NIFTYMIDSML400',instrument_key=BENCH,unit='days',interval=1,from_date='2025-12-01',to_date='2026-02-01'))
    from .upstox_candles import url_for
    from types import SimpleNamespace
    for i,r in enumerate(requests,1):
        r['number']=i
        r['url']=url_for(SimpleNamespace(**{**r,'from_date':date.fromisoformat(r['from_date']),'to_date':date.fromisoformat(r['to_date'])}))
    plan=dict(scope='ER-03:12-COMPANIES:DAILY-ONLY:CUTOFF-20261030',status='NOT_AUTHORIZED_NOT_EXECUTED',
        request_count=len(requests),requests=requests,metadata_path=str(metadata_path.relative_to(ROOT)),metadata_sha256=digest(metadata_path),
        rationale='10 missing pilot stocks, AETHER extension, benchmark warmup extension; retained TIPSMUSIC and remaining benchmark reused',
        warmup='2025-12-01 gives >55 sessions before earliest April event; verify returned counts; existing first-non-na EMA/RMA seeds retained',
        mechanism='Existing Upstox V3 historical-candle URL/parser and execute_ta02d.secure_supplier; no production acquisition changes',
        authorization='Fresh owner approval required for exact manifest SHA256; existing TA-02B/TA-03R4 approval is exhausted and narrower',
        token_accessed=False,network_requests_made=0,read_only=True,automatic_retries=0,
        execution_policy='Serial >=1s apart; first GET entitlement probe; stop on any HTTP/schema/transport error or empty response; no substitutions',
        retention_location='artifacts/earnings-reaction-v1/er03/provider/',
        adjustment_basis='UNKNOWN_PROVIDER_ADJUSTMENT_BASIS; no invented adjustment')
    if not (OUT/'acquisition-manifest.json').exists():
        write_json(OUT/'acquisition-manifest.json',plan)
    (OUT/'acquisition-manifest.sha256').write_text(digest(OUT/'acquisition-manifest.json')+'  acquisition-manifest.json\n')
    write_csv(OUT/'pilot-companies.csv',companies);write_csv(OUT/'approximate-event-windows.csv',events)
    write_csv(OUT/'price-performance.csv',results)
    # Reuse proposed peer memberships instead of inventing constituent sets.
    peers=[r for r in read_csv(ROOT/'artifacts/subsector-ss04/COMPANY_SUBSECTOR_FINAL.csv') if r['primary_code'] in ('EMS','TRANSFORMERS','TYRES')]
    write_csv(OUT/'proposed-peer-groups.csv',[dict(company_id=r['company_id'],company_name=r['company_name'],subsector=r['primary_code'],status=r['status'],known_at=r['known_at'],source='SS04 primary assignments; proposed research peer group, not tutor index') for r in peers])
    sector_meta={}; sector_results=[]
    for code in ('EMS','TRANSFORMERS','TYRES'):
        members=[symbol for cid,symbol in PILOT if classes[cid]['primary_code']==code and symbol in all_features]
        index_rows,info=subsector_index({symbol:all_features[symbol] for symbol in members})
        sector_meta[code]=dict(proposed_peers=sum(r['primary_code']==code for r in peers),**info)
        if index_rows:
            write_csv(OUT/'subsector'/f'{code}-index.csv',index_rows)
            bydate={r['date']:r['index_close'] for r in index_rows}
            for result in results:
                if result['symbol'] not in members:continue
                first,last=result['first_date'],result['last_date']
                obs=all_features[result['symbol']]; first_close=next(r['close'] for r in obs if r['date']==first)
                last_close=next(r['close'] for r in obs if r['date']==last)
                peer_return=100*(bydate[last]/bydate[first]-1)
                stock_return=100*(last_close/first_close-1)
                sector_results.append(dict(event_id=result['event_id'],subsector=code,first_date=first,last_date=last,
                    stock_close_to_close_return_pct=stock_return,peer_index_close_to_close_return_pct=peer_return,
                    stock_minus_peer_return_pp=stock_return-peer_return,peer_count=len(members),
                    includes_stock_in_peer_index=True,interpretation='Descriptive; pilot subset, not complete subsector or tutor index'))
    write_csv(OUT/'subsector-comparisons.csv',sector_results)
    write_json(OUT/'subsector-comparisons.json',dict(status='PILOT_SUBSET_EQUAL_INITIAL_WEIGHT_CLOSE_INDICES',
        coverage=sector_meta,
        methodology='Existing SS02 Alternative 1: 100/N * sum(P_i(t)/P_i(t0)); fixed initial equal weights; exact common date sequences; no filling or dropped sessions; require >=3 peers. Base is first common close. Weights drift after formation. No rebalancing. Corporate-action basis unknown.',
        membership='Available named pilot members of existing SS04 primary peer proposals, fixed without return-based selection. Not the full proposed group; retrospectively formed.',
        comparison='Stock and index compared close-to-close on identical dates; index includes the stock, not an independent peer benchmark. Stock event performance separately uses entry OPEN.',
        equivalence='Does not reproduce tutor TradingView subsector candles or volume profile; closing-price index only',
        individual_stock_analysis_blocked=False))
    cohorts=[]
    for quarter,horizon in [('FY2026-Q4',''),('FY2027-Q1','H20'),('FY2027-Q1','H40')]:
        pool=[r for r in results if r['quarter']==quarter and (not horizon or r['event_id'].endswith(horizon))]
        for decision in ['ALL','MENTOR_SELECTED_FOCUS_REFERENCE','UNKNOWN_NOT_REJECTED']:
            sample=[r for r in pool if decision=='ALL' or r['mentor_decision']==decision]
            if not sample:continue
            cohorts.append(dict(quarter=quarter,horizon=horizon or 'BEFORE_NEXT_RESULTS',label_group=decision,
                observations=len(sample),positive_return_count=sum(r['return_pct']>0 for r in sample),
                median_return_pct=median(r['return_pct'] for r in sample),
                median_close_drawdown_pct=median(r['maximum_close_drawdown_pct'] for r in sample),
                median_above_ema20_fraction=median(Decimal(r['above_ema20_sessions'])/r['observations'] for r in sample),
                median_positive_rs55_fraction=median(Decimal(r['rs55_positive_sessions'])/r['rs55_available_sessions'] for r in sample if r['rs55_available_sessions']),
                comparison_status='DESCRIPTIVE_ONLY; unknown group is not rejected; overlapping horizons not independent'))
    write_csv(OUT/'descriptive-cohort-comparison.csv',cohorts)
    h20={r['symbol']:r for r in results if r['event_id'].endswith('H20')}
    h40={r['symbol']:r for r in results if r['event_id'].endswith('H40')}
    fading=sum(h40[s]['return_pct']<r['return_pct'] for s,r in h20.items() if s in h40)
    combined_indices_chart(OUT/'charts/subsector-indices.svg',OUT/'subsector')
    write_json(OUT/'label-audit.json',dict(registry_rows=len(labels),positive_labels=sum(r['label']==1 for r in labels),explicit_rejected_labels=0,
        derived_reject_pool='artifacts/focus_ml/governance/FG00_UNIVERSE_DIAGNOSIS.md: derived rejects remain UNKNOWN, not authoritative mentor rejections',
        selected_vs_rejected_comparison='UNSUPPORTED; no authoritative explicitly rejected mentor labels',
        pilot_decisions={d:sum(c['mentor_decision']==d for c in events) for d in set(c['mentor_decision'] for c in events)}))
    write_json(OUT/'reproducibility.json',dict(command='python3 -m experiments.earnings_reaction_v1.er03',tests='python3 -m unittest experiments.earnings_reaction_v1.tests.test_er03',
        code_sha256=digest(Path(__file__)),python='Python standard library only',retained_candle_hashes=input_hashes,
        source_records_sha256=digest(OUT/'retained-results-records.json'),database=str(DB.relative_to(ROOT)),
        database_access='read-only SQLite, bounded symbols/periods; exact queried records retained; full 14.6GB DB not hashed'))
    after={str(p.relative_to(ROOT)):digest(p) for p in old.rglob('*') if p.is_file()}
    assert after==preserved,'earlier event evidence changed'
    write_json(OUT/'preservation-verification.json',dict(unchanged=True,files_checked=len(after),before=preserved,after=after))
    report=['# ER-03 actual historical pilot\n',
        f'12 unique companies; Q4 and Q1 for each. {len(events)} observation windows, {len(results)} analyzed from retained real Upstox daily candles. All windows are APPROXIMATE. No announcement-time verification or exchange scraping was performed.\n',
        'Daily CSVs include all retained warmup bars, EMA10/20, source ATR14/RMA (first true range unavailable), daily range, RVOL20/21 using prior-session volume averages, and existing dimensionless RS55 against NIFTYMIDSML400. RS is blank wherever exact 56-bar benchmark alignment is unavailable. Features use only the current and earlier completed bars.\n',
        'Returns start at the first observed session OPEN after the documented results date and end at the last observed close before the next results date. That excludes the announcement-to-next-open gap. Max rise and low return use daily highs/lows relative to entry; drawdown is running closing-peak to close. Unknown next dates produce separate 20/40-session horizons. All 12 stocks now have data through October 30; censoring is explicit.\n',
        '## Measured outcomes\n']
    for r in results:
        report.append(f'- {r["event_id"]}: {r["first_date"]} to {r["last_date"]}, {r["observations"]} observed sessions; return {r["return_pct"]:.2f}%, max rise {r["maximum_rise_pct"]:.2f}%, close drawdown {r["maximum_close_drawdown_pct"]:.2f}%; RVOL20 >=2 on {r["rvol20_ge2_sessions"]} sessions ({r["up_close_rvol20_ge2_sessions"]} up-close, {r["down_close_rvol20_ge2_sessions"]} down-close; excludes first session from directional counts); above EMA20 on {r["above_ema20_sessions"]}; positive RS on {r["rs55_positive_sessions"]}/{r["rs55_available_sessions"]} available sessions; right censored={r["right_censored"]}.\n')
    report += ['\n## Observations and limits\n',
        f'Across the 12-stock pilot, Q4 had 9/12 positive endpoint returns and median +8.38%; Q1 had 7/12 positive at 20 observations (median +2.91%) and 5/12 at 40 (median -1.87%). Q4 windows have different durations. In Q1, {fading}/12 returns declined between observation 20 and 40. These are descriptive, overlapping horizon comparisons. PGEL changed from +29.30% in Q4 to -20.61% at Q1 H40, while SYRMA reached +23.18% at Q1 H40; the shared EMS label did not imply uniform stock behavior.\n',
        'AETHER Q4 closed above EMA20 in 40/52 observations and had positive RS55 in 42/52. TIPSMUSIC Q1 had positive RS55 on only 6/20 and 11/40 observations despite positive endpoint returns; its maximum rise of 15.19% faded to 3.90% and 4.68%. These are candidate continuation-versus-fading behaviors to examine across the acquired pilot, not a demonstrated recurring pattern.\n',
        'The 12-stock pilot permits descriptive case comparisons only. Large relative-volume sessions occur on both rising and falling closes; elevated volume alone is not evidence of sustained strength. Compare the EMA20 and RS persistence counts above with each return, without treating these retrospective features as entry signals. No statistical significance, recurring predictive pattern, strategy performance or selected-versus-rejected advantage is established. The two Q1 horizons overlap and are not independent events.\n',
        f'{len(quality)} weekend provider bars are retained and flagged; all are the already documented February 1 special session (see existing indicators.SPECIAL_SESSION_SOURCE). These data are real retained provider responses, but market session validity and completeness are not certified. Horizon counts mean observed provider sessions provisionally, not a verified exchange calendar. Missing sessions, corporate actions, adjusted-price basis, historical symbol continuity and retrospective cohort/subsector selection remain limitations. Charts display provider bars without inventing corrections.\n',
        'Existing B2 decision-universe context is attached unchanged for all 12 companies in existing-financial-research-context.csv, preserving its PIT_NOT_VERIFIED status. It is retrospective context only. Focus positives and unknown labels remain distinct; unknown labels are never converted into rejections. These labels cannot establish a selection advantage against explicitly rejected stocks because none are authoritative.\n',
        'No authoritative explicit rejected mentor labels were found. Focus absence stays UNKNOWN. Q1 derived rejected candidates cannot be treated as explicit mentor rejection. See label-audit.json. Current SS04 classifications attach as retrospective context with known_at preserved, never backdated. EMS/Transformers/Tyres groups are listed. Available fixed pilot subsets support initial-equal-weight closing-price indices; all named constituents and exact alignment checks are recorded. They do not reproduce tutor subsector candles or volume profiles.\n',
        f'## Acquisition completed\n\nThe exact {len(requests)} daily GET requests in acquisition-manifest.json were owner-approved and executed. The immutable approved manifest describes the pre-execution state; retrieval-execution.json is the execution authority and receipt. All 12 returned HTTP 200. No further market-data authorization is needed for this bounded pilot. No ML or production acquisition changes.\n',
        '## Reproduce\n\nRun `python3 -m experiments.earnings_reaction_v1.er03`, then `python3 -m unittest experiments.earnings_reaction_v1.tests.test_er03`. Use charts/index.html to view actual stock charts. CSV blanks mean unavailable, never zero. All ER01/ER02 files were SHA256 checked before and after.\n']
    sourcecopy=OUT/'code';sourcecopy.mkdir(exist_ok=True)
    for source in [Path(__file__),Path(__file__).parent/'retrieve_er03.py',Path(__file__).parent/'tests/test_er03.py']:
        (sourcecopy/source.name).write_bytes(source.read_bytes())
    (OUT/'README.md').write_text('\n'.join(report))
    svgs=sorted((OUT/'charts').glob('*.svg'))
    (OUT/'charts/index.html').write_text('<!doctype html><meta charset="utf-8"><title>ER-03 actual stock charts</title><h1>ER-03 · APPROXIMATE windows</h1><p>Real retained Upstox candles; provisional session calendar and adjustment basis. Read ../README.md for limits.</p>'+''.join(f'<h2>{p.stem}</h2><img alt="{p.stem}: price, volume, RVOL and RS55" src="{p.name}" style="width:100%;max-width:1040px">' for p in svgs))
    write_json(OUT/'summary.json',dict(status='BOUNDED_PILOT_COMPLETE',unique_companies=12,quarter_events=24,observation_windows=len(events),analyzed_quarter_events=len({(r["symbol"],r["quarter"]) for r in results}),analyzed_windows=len(results),daily_rows=sum(map(len,all_features.values())),requests_needing_approval=0,earlier_evidence_unchanged=True,subsector_indices_calculated=sum(v["status"]=="CALCULATED" for v in sector_meta.values()),explicit_rejected_labels=0))
    print(json.dumps(dict(companies=len(companies),windows=len(events),analyzed=len(results),daily_series=len(all_features),requests_needing_approval=0,preserved_files=len(after),quality_flags=len(quality))))

if __name__=='__main__':main()
