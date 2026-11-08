"""Read the real archive and append current chart views, preserving old studies.

All indicator arithmetic is imported from the verified implementations.
"""
from collections import defaultdict, Counter
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import csv
import gzip
import json
from pathlib import Path
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

from experiments.historical_candle_archive_v1.access import HistoricalStore
from experiments.historical_candle_archive_v1.calendar import day_record
from experiments.historical_candle_archive_v1.storage import ROOT, dump, sha
from experiments.historical_candle_archive_v1.indicator_check import candle
from experiments.technical_integration_v2 import build as old
from experiments.technical_integration_v2.narrowed_validation import validate, spread_bp, yield_delta_bp
from experiments.earnings_reaction_v1.indicators import pine_average, _dtr_arithmetic, calculate_chart_rs, merge_daily_dtr, calculate_daily
from experiments.earnings_reaction_v1.simple_volume import simple_volume_features
from experiments.earnings_reaction_v1.indicator_alternatives import aggregate, dss_rvol_reconstruction
from experiments.earnings_reaction_v1.candles import Candle
from experiments.earnings_reaction_v1.events import Session
from .update import OUT, MANIFEST

CHARTS=ROOT/'artifacts/technical-integration-v2/charts'
IST=ZoneInfo('Asia/Kolkata')


def read(path):
    with Path(path).open(newline='') as f:return list(csv.DictReader(f))


def csvout(path, rows):
    rows=list(rows)
    with Path(path).open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(dict.fromkeys(k for r in rows for k in r)))
        w.writeheader();w.writerows(rows)


def pack(data):
    return json.dumps(data,default=lambda x:float(x) if isinstance(x,Decimal) else str(x),separators=(',',':')).replace('</','<\\/')


def updates(store):
    ledger=OUT/'UPDATE_LEDGER.json'
    if not ledger.exists():return [],[],[]
    sources=[(MANIFEST,ledger)]
    recurring=OUT/'recurring'
    grant=recurring/'OWNER_GRANT.json';proposal=recurring/'PROPOSED_MANIFEST.json'
    if grant.exists() and proposal.exists():
        parent=sha(proposal.read_bytes())
        if json.loads(grant.read_text()).get('manifest_sha256')!=parent:
            raise RuntimeError('STOP_RECURRING_CHART_GRANT_MISMATCH')
        for saved in sorted((recurring/'batches').glob('*/UPDATE_LEDGER.json')):
            manifest=saved.parent/'manifest.json'
            if json.loads(manifest.read_text()).get('parent_manifest_sha256')!=parent:
                raise RuntimeError('STOP_RECURRING_CHART_PARENT_MISMATCH')
            sources.append((manifest,saved))
    records=[]
    for manifest,saved in sources:
        state=json.loads(saved.read_text())
        if state['manifest_sha256']!=sha(manifest.read_bytes()):
            raise RuntimeError('STOP_CHART_LEDGER_MANIFEST_MISMATCH')
        queries={r['request_id']:r for r in json.loads(manifest.read_text())['requests']}
        records.extend((ident,rec,queries[ident]) for ident,rec in state['requests'].items())
    bars=[];treasury=[];quarantine=[]
    for ident,rec,q in sorted(records,key=lambda r:r[1].get('received_at','')):
        if rec['status'] not in ('SUCCESS','EMPTY') or rec['kind']=='STOCK':continue
        raw=gzip.decompress((store.root/rec['raw_path']).read_bytes())
        if sha(raw)!=rec['sha256']:raise RuntimeError('STOP_INTEGRATION_RAW_CHECKSUM')
        if rec['kind']=='TREASURY':
            for entry in ET.fromstring(raw).findall('{http://www.w3.org/2005/Atom}entry'):
                prop=entry.find('.//{http://schemas.microsoft.com/ado/2007/08/dataservices/metadata}properties')
                v={r.tag.split('}')[-1]:r.text for r in prop}
                day=v['NEW_DATE'][:10]
                if day>q.get('to_date','2026-11-06'):continue
                vals={k:Decimal(v[n]) if v.get(n) not in (None,'','N/A') else None for k,n in [('UST2Y','BC_2YEAR'),('UST10Y','BC_10YEAR'),('UST30Y','BC_30YEAR')]}
                quote=datetime.fromisoformat(day+'T15:30:00').replace(tzinfo=ZoneInfo('America/New_York'))
                treasury.append(dict(date=day,**vals,spread_10y_2y_bp=spread_bp(vals['UST2Y'],vals['UST10Y']),
                    observation_convention_ist=quote.astimezone(IST).isoformat(),
                    timing_basis='Approximate Treasury quotation convention; not historical publication',
                    source_file=str(store.root/rec['raw_path']),source_sha256=rec['sha256'],retrieved_at=rec['received_at']))
        else:
            good,bad,_=validate(json.loads(raw)['data']['candles'],dict(q,number=ident),rec['received_at'])
            for r in good:r.update(instrument_key=q['instrument_key'],kind=q['kind'],source_file=str(store.root/rec['raw_path']),source_sha256=rec['sha256'],retrieved_at=rec['received_at'])
            bars.extend(good);quarantine.extend(bad)
    return bars,treasury,quarantine


def indices(new):
    inventory=read(old.OUT/'UPSTOX_ALL_INDICES.csv')
    lookup={r['instrument_key']:r for r in inventory}
    bars={};checked={}
    # All original official-index OHLC fields come from their retained response.
    for r in read(old.OUT/'MARKET_DATA_PROVENANCE.csv'):
        if r.get('instrument_key') not in lookup or not r.get('value'):continue
        p=ROOT/r['source_file']
        if p.suffix!='.json':continue
        if p not in checked:
            if sha(p.read_bytes())!=r['source_sha256']:raise RuntimeError('STOP_RETAINED_INDEX_HASH')
            checked[p]={b[0][:10]:b for b in json.loads(p.read_text())['data']['candles']}
        b=checked[p].get(r['observation_date'])
        if b is None:continue
        if not 0<float(b[3])<=min(float(b[1]),float(b[4]))<=max(float(b[1]),float(b[4]))<=float(b[2]):continue
        bars[r['instrument_key'],r['observation_date']]=dict(instrument_key=r['instrument_key'],date=r['observation_date'],open=b[1],high=b[2],low=b[3],close=b[4],source_file=r['source_file'],source_sha256=r['source_sha256'])
    for r in new:
        if r['kind']=='NSE_INDEX':bars[r['instrument_key'],r['provider_bucket_date']]=dict(r,date=r['provider_bucket_date'])
    # No closed-session provider label enters official NSE session comparisons.
    bars={k:r for k,r in bars.items() if day_record(k[1])['sessions']}
    latest=max((r['date'] for r in bars.values()),default='2026-10-30')
    days=[];d=date(2025,12,1)
    while str(d)<=latest:
        if day_record(d)['sessions']:days.append(str(d))
        d+=timedelta(days=1)
    points=[dict(series='NSE_PRICE',instrument_key=key,observation_date=day,value=str(r['close'])) for (key,day),r in bars.items()]
    for r in inventory:
        part=[x for (key,day),x in bars.items() if key==r['instrument_key']]
        r.update(daily_history_verified=bool(part),retained_daily_observations=len(part),
            first_verified_provider_date=min((x['date'] for x in part),default=''),last_verified_provider_date=max((x['date'] for x in part),default=''),
            history_status='ACQUIRED_RETROSPECTIVE' if part else 'NO_ACCEPTED_HISTORY',
            missing_sessions=sum(d not in {x['date'] for x in part} for d in days),
            warning='Launch/backcalculation and historical delivery unverified; missing dates are not filled')
    rotation=old.rotation(points,inventory,days,write_output=False,include_all_nse=True)
    features={}
    for key in lookup:
        chains=[];chain=[]
        for day in days:
            row=bars.get((key,day))
            if row is None:
                if chain:chains.append(chain);chain=[]
            else:
                start=datetime.fromisoformat(day+'T00:00:00+05:30');end=start+timedelta(days=1)
                chain.append(Candle(key,start.isoformat(),start,end,*(Decimal(str(row[k])) for k in ('open','high','low','close')),Decimal(0),Decimal(0),'ORIGINAL_INDEX_OHLC',end,day))
        if chain:chains.append(chain)
        for chain in chains:
            # Reuse the validated price arithmetic. Zero index share-volume is
            # never exposed as a meaningful volume/flow indicator.
            for r in calculate_daily(tuple(chain),tuple(chain),cutoff=datetime.now(timezone.utc)):
                features[key,str(r.stock.start_ist.date())]=dict(atr14=r.atr,dtr_atr_pct=r.dtr_pct)
    for r in rotation:r.update(features.get((r['instrument_key'],r['date']),dict(atr14=None,dtr_atr_pct=None)))
    csvout(OUT/'NSE_INDEX_COVERAGE.csv',inventory);csvout(OUT/'NSE_ROTATION.csv',rotation)
    return dict(inventory=inventory,bars=list(bars.values()),rotation=rotation,days=days)


def globals_data(new,yields):
    p=CHARTS/'global-minute-data.js'
    oldbars=json.loads(p.read_text().removeprefix('window.TECH06_GLOBAL_BARS=').strip().removesuffix(';'))
    groups={(r['series'],r['unit'],r['timestamp']):r for r in oldbars}
    # S&P existed in the original source context but was omitted from the later
    # recovered chart selector. Restore its original OHLC, without date shifts.
    for r in read(old.OUT/'MARKET_DATA_PROVENANCE.csv'):
        if r['series']!='SP500':continue
        path=ROOT/r['source_file']
        if sha(path.read_bytes())!=r['source_sha256']:raise RuntimeError('S&P retained source hash')
        for b in json.loads(path.read_text())['data']['candles']:
            q=dict(number='RETAINED_SP500',series='SP500',unit='days',interval=1,from_date='2025-01-01',to_date='2026-10-30')
            accepted,_,_=validate([b],q,r['retrieved_at'])
            for a in accepted:
                groups['SP500','days',a['provider_timestamp']]=dict(series='SP500',unit='days',timestamp=a['provider_timestamp'],date=a['provider_bucket_date'],open=a['open'],high=a['high'],low=a['low'],close=a['close'],end_ist=a['end_ist'],source_file=r['source_file'])
        break
    for r in new:
        if r['kind']=='GLOBAL':groups[r['series'],r['unit'],r['provider_timestamp']]=dict(r,timestamp=r['provider_timestamp'],date=r['provider_bucket_date'])
    data=json.loads((CHARTS/'global-market-data.js').read_text().removeprefix('window.TECH06_GLOBAL=').strip().removesuffix(';'))
    oldy={r['date']:r for r in data['treasury']}
    for r in yields:oldy[r['date']]=r
    ys=sorted(oldy.values(),key=lambda r:r['date']);previous=None
    for r in ys:
        if previous:
            for k in ('UST2Y','UST10Y','UST30Y'):r[k+'_change_bp']=yield_delta_bp(r[k],previous[k])
            r['spread_change_bp']=None if r['spread_10y_2y_bp'] is None or previous['spread_10y_2y_bp'] is None else Decimal(str(r['spread_10y_2y_bp']))-Decimal(str(previous['spread_10y_2y_bp']))
            r['change_base_date']=previous['date']
        previous=r
    status=[]
    for key in ('DJIA','SP500','GIFT_NIFTY','USDINR','BRENT','WTI','US_TECH100'):
        rows=sorted((r for r in groups.values() if r['series']==key and r['unit']=='days'),key=lambda r:r['date'])
        status.append(dict(series=key,label='US Tech 100 · provider proxy' if key=='US_TECH100' else key,
            daily=len(rows),minute=sum(r['series']==key and r['unit']=='minutes' for r in groups.values()),
            first=rows[0]['date'] if rows else None,last=rows[-1]['date'] if rows else None,
            units='Provider quote units unverified',warning='Cash-session mapping quarantined; original provider buckets' if key in ('DJIA','SP500') else 'Generic quotation; contract/unit definition unverified' if key=='GIFT_NIFTY' else 'Retrospective provider quotation; historical delivery unverified'))
    csvout(OUT/'GLOBAL_COVERAGE.csv',status);csvout(OUT/'TREASURY_YIELDS.csv',ys)
    return dict(status=status,bars=list(groups.values()),treasury=ys)


def chart_indicators(rows,timeframe,exclusions,daily_rows=(),benchmark=(),daily_exclusions=()):
    """Reset at exclusions; never compress a known missing session into a seed."""
    result={};gaps=sorted({x['day'] for x in (*exclusions,*daily_exclusions)});segments=[];part=[]
    for r in rows:
        if part and any(part[-1]['start'][:10]<=d<=r['start'][:10] for d in gaps):
            segments.append(part);part=[]
        part.append(r)
    if part:segments.append(part)
    for segment in segments:
        bars=tuple(candle(r) for r in segment)
        ema10=pine_average([c.close for c in bars],10,'EMA')
        ema20=pine_average([c.close for c in bars],20,'EMA')
        dtr=_dtr_arithmetic(bars) if timeframe=='Daily' else None
        cutoff=max(c.end_ist for c in bars)
        seed_floor=max((d for d in gaps if d<segment[0]['start'][:10]),default='')
        daily=tuple(candle(r) for r in daily_rows if r['start'][:10]>seed_floor)
        volume=simple_volume_features(bars,cutoff=cutoff,daily=timeframe=='Daily',weekly=timeframe=='Weekly',daily_history=tuple(c for c in daily if c.completed_at<=cutoff))
        causal=merge_daily_dtr(bars,tuple(c for c in daily if c.completed_at<=cutoff),cutoff=cutoff,timeframe=timeframe) if timeframe in ('1-hour','4-hour') else None
        rs={};dss={};rs_reason='WEEKLY_CONTEXT_UNSUPPORTED' if timeframe=='Weekly' else 'EXACT_BENCHMARK_BOUNDARIES_UNAVAILABLE'
        if timeframe!='Weekly' and benchmark:
            # Only a contiguous, exactly aligned prefix may use the benchmark.
            # A missing benchmark bar breaks the seed; no inner join of gaps.
            bm={(c.start_ist,c.end_ist):c for c in benchmark};chains=[];chain=[]
            for c in bars:
                if (c.start_ist,c.end_ist) in bm:chain.append(c)
                elif chain:chains.append(chain);chain=[]
            if chain:chains.append(chain)
            for chain in chains:
                matching=tuple(bm[c.start_ist,c.end_ist] for c in chain)
                values=calculate_chart_rs(tuple(chain),matching,cutoff=cutoff,timeframe=timeframe)
                rs.update({c.start_ist.isoformat():v for c,v in zip(chain,values)})
        if timeframe in ('1-hour','4-hour'):
            sessions=tuple(Session(date.fromisoformat(d),datetime.fromisoformat(s['opens_at']),datetime.fromisoformat(s['closes_at']))
                for d in sorted({r['start'][:10] for r in segment}) for s in day_record(d)['sessions'])
            try:
                ds=dss_rvol_reconstruction(bars,sessions,cutoff=cutoff,bar_minutes=60 if timeframe=='1-hour' else 240)
                dss={c.start_ist.isoformat():v for c,v in zip(bars,ds)}
            except ValueError:
                pass  # Known incomplete owner sessions remain visible, never filled.
        for i,r in enumerate(segment):
            result[r['start']]=dict(ema10=ema10[i],ema20=ema20[i],atr14=dtr[1][i] if dtr else None,
                rvol20=dtr[2][i] if dtr else None,volume=volume[i] if volume else None,
                rs=rs.get(r['start']),rs_status='EXACT_ALIGNED_RETAINED_BENCHMARK' if r['start'] in rs else rs_reason,
                dss=(dict(dss_ratio=dtr[2][i],dss_pct=100*dtr[2][i] if dtr[2][i] is not None else None) if dtr else dss.get(r['start'])),causal_daily_dtr=causal[i]['causal_last_completed_daily'] if causal else None,
                causal_daily_dependency_at=causal[i]['causal_dependency_at'] if causal else None)
    return result


def benchmark_context(nse):
    key='NSE_INDEX|NIFTY MIDSML 400';daily=[];minutes=[]
    for r in nse['bars']:
        if r['instrument_key']!=key:continue
        start=datetime.fromisoformat(r['date']+'T00:00:00+05:30');end=start+timedelta(days=1)
        daily.append(Candle(key,start.isoformat(),start,end,*(Decimal(str(r[k])) for k in ('open','high','low','close')),Decimal(0),Decimal(0),'RETAINED_PROVIDER_DAILY',end,r['date']))
    ledger=json.loads((ROOT/'artifacts/earnings-reaction-v1/ta03r4/retrieval-execution.json').read_text())
    for rec in ledger['requests']:
        if 'NIFTY%20MIDSML%20400/minutes/5' not in rec['url']:continue
        p=ROOT/rec['retained_path']
        if sha(p.read_bytes())!=rec['response_sha256']:raise RuntimeError('STOP_BENCHMARK_HASH')
        for b in json.loads(p.read_text())['data']['candles']:
            start=datetime.fromisoformat(b[0]);end=start+timedelta(minutes=5)
            minutes.append(Candle(key,b[0],start,end,*(Decimal(str(v)) for v in b[1:]),'RETAINED_5_MINUTE',end,str(start.date())))
    contexts={'Daily':tuple(sorted(daily,key=lambda c:c.start_ist))}
    dates=sorted({c.start_ist.date() for c in minutes});sessions=tuple(Session(d,datetime.fromisoformat(s['opens_at']),datetime.fromisoformat(s['closes_at'])) for d in dates for s in day_record(d)['sessions'])
    for tf in ('1-hour','4-hour'):
        contexts[tf]=aggregate(tuple(sorted(minutes,key=lambda c:c.start_ist)),sessions,tf,cutoff=datetime.now(timezone.utc),base_minutes=5)
    return contexts


def archive_data(store,ids,cutoff,nse,window_start=None,window_end='2026-11-06'):
    manifest=json.loads((ROOT/'artifacts/historical-candle-archive-v1/full-universe-manifest.json').read_text())
    companies={r['company_id']:r for r in manifest['companies']+manifest.get('historically_eligible_extension',[])}
    result=[];reconcile=[];benchmarks=benchmark_context(nse)
    for cid in ids:
        daily=store.read(cid,'Daily',start=min(window_start or '2025-12-01','2025-12-01'),end=window_end,decision_time=cutoff,mode='retrospective')
        for tf in ('Weekly','Daily','4-hour','1-hour'):
            start=window_start or ('2024-01-01' if tf=='Weekly' else '2025-12-01' if tf=='Daily' else '2026-08-17')
            history=store.read(cid,tf,start=start,end=window_end,decision_time=cutoff,mode='retrospective')
            indicators=chart_indicators(history['rows'],tf,history['exclusions'],daily['rows'],benchmarks.get(tf,()),daily['exclusions'])
            rows=[]
            for r in history['rows']:
                item={k:r[k] for k in ('start','open','high','low','close','volume','status','provider_timestamp','observed_at','source_response_sha256s')}
                item.update(end=r.get('end') or r['end_upper_bound'],indicators=indicators.get(r['start'],{}))
                rows.append(item)
                # Export only actual stored/aggregated values; browser compares
                # every rendered OHLC field with this independent access output.
                reconcile.append(dict(company_id=cid,timeframe=tf,start=item['start'],end=item['end'],**{k:item[k] for k in ('open','high','low','close','volume')}))
            developing=store.developing(cid,tf,decision_time='2026-10-30T14:00:00+05:30') if tf in ('1-hour','4-hour') else dict(rows=[],exclusions=[])
            result.append(dict(company_id=cid,symbol=companies[cid].get('symbol',str(cid)),timeframe=tf,rows=rows,
                exclusions=history['exclusions'],developing=developing,assumptions=history['assumptions'],
                warnings=['Provider adjustment basis uncertified; no local factor applied','Indicators reset at known gaps; warmup is explicit',
                    'Weekly DSS/benchmark RS and intraday benchmark RS need aligned validated inputs; not synthesized'],source_revisions=history['source_revisions']))
    csvout(OUT/'ARCHIVE_CHART_VALUES.csv',reconcile)
    return result


def main(ids=(555,90,3),start=None,end='2026-11-06'):
    store=HistoricalStore()
    try:
        new,yields,quarantine=updates(store)
        nse=indices(new);globaldata=globals_data(new,yields)
        cutoff=datetime.now(timezone.utc).isoformat()
        archive=archive_data(store,ids,cutoff,nse,start,end)
        inv=[dict(r) for r in store.db.execute("SELECT unit,interval,COUNT(DISTINCT company_id) AS companies,SUM(row_count) AS receipt_rows,MIN(from_date) AS requested_first,MAX(to_date) AS requested_last FROM requests WHERE status='SUCCESS' GROUP BY unit,interval")]
        states=dict(store.db.execute('SELECT status,COUNT(*) FROM requests GROUP BY status').fetchall())
        summary=dict(generated_at=cutoff,passport_uuid=store.info['VolumeUUID'],archive_inventory=inv,archive_request_states=states,
            nse_histories=sum(r['daily_history_verified'] for r in nse['inventory']),nse_catalog=len(nse['inventory']),
            nse_categories=dict(Counter(r['classification'] for r in nse['inventory'])),global_coverage=globaldata['status'],
            treasury_dates=len(globaldata['treasury']),archive_chart_series=len(archive),new_quarantined=len(quarantine))
        dump(OUT/'CURRENT_COVERAGE.json',summary);dump(OUT/'NEW_QUARANTINE.json',quarantine)
        payload=dict(summary=summary,nse=nse,globaldata=globaldata,archive=archive)
        (CHARTS/'technical-system-data.js').write_text('window.TECH_FIX_02='+pack(payload)+';\n')
        from .page import PAGE
        (CHARTS/'technical-system.html').write_text(PAGE)
        index=CHARTS/'index.html';link='\n<p><a href="technical-system.html">TECH-FIX-02 · current NSE, globals and Passport timeframes</a></p>\n'
        original=index.read_text().replace(link,'')
        index.write_text(original.replace('</h1>','</h1>'+link,1))
        print(json.dumps(summary,default=str),flush=True)
    finally:store.close()


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--company',nargs='+',type=int,default=[555,90,3]);p.add_argument('--start');p.add_argument('--end',default='2026-11-06')
    args=p.parse_args();main(tuple(args.company),args.start,args.end)
