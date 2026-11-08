"""Compatibility checks call preserved indicator implementations; no formula changes."""
from dataclasses import asdict,replace
from datetime import datetime,date,timedelta,timezone
from decimal import Decimal
import gzip
import json
from .storage import Archive,OUT,ROOT,dump,sha
from .analyze import load
from .calendar import day_record
from .prepare import csvout
from experiments.earnings_reaction_v1.candles import Candle
from experiments.earnings_reaction_v1.events import Session
from experiments.earnings_reaction_v1.indicators import pine_average,_dtr_arithmetic,calculate_chart_rs,merge_daily_dtr
from experiments.earnings_reaction_v1.simple_volume import simple_volume_features
from experiments.earnings_reaction_v1.indicator_alternatives import aggregate,dss_rvol_reconstruction

def candle(r):
    start=datetime.fromisoformat(r['start'])
    end=datetime.fromisoformat(r.get('end') or r['end_upper_bound'])
    if r['timeframe']=='Daily': end=start+timedelta(days=1)
    return Candle(r['instrument_key'],r['provider_timestamp'],start,end,
                  *(Decimal(r[k]) for k in ('open','high','low','close','volume','open_interest')),
                  'HCD01_RETAINED_PROVIDER',end,r['start'][:10])

def main():
    a=Archive();groups,_,_,_=load(a);cutoff=datetime.now(timezone.utc)
    ledger=json.loads((ROOT/'artifacts/earnings-reaction-v1/ta03r4/retrieval-execution.json').read_text())
    benchmark_daily=[];benchmark_minutes=[]
    for receipt in ledger['requests']:
        if 'NIFTY%20MIDSML%20400' not in receipt['url']: continue
        path=ROOT/receipt['retained_path'];raw=path.read_bytes()
        if sha(raw)!=receipt['response_sha256']: raise ValueError('retained benchmark checksum')
        rows=json.loads(raw)['data']['candles'];daily='/days/' in receipt['url']
        for r in rows:
            start=datetime.fromisoformat(r[0]);end=start+(timedelta(days=1) if daily else timedelta(minutes=5))
            bar=Candle('NSE_INDEX|NIFTY MIDSML 400',r[0],start,end,
                       *(Decimal(str(v)) for v in r[1:]),'REUSED_TA03R4_BENCHMARK',end,str(start.date()))
            (benchmark_daily if daily else benchmark_minutes).append(bar)
    benchmark_daily=tuple(sorted(benchmark_daily,key=lambda c:c.start_ist))
    benchmark_minutes=tuple(sorted(benchmark_minutes,key=lambda c:c.start_ist))
    results=[];computed=[]
    pilot=json.loads((OUT/'pilot-companies.json').read_text())
    for company in pilot:
        cid=company['company_id']
        daily=tuple(candle(r) for r in groups.get((cid,'Daily'),[]) if r['quality']=='OHLC_VALID' and r['start'][:10]>='2022-01-01')
        for tf in ('Daily','Weekly','1-hour','4-hour'):
            rows=groups.get((cid,tf),[])
            if tf=='Daily':rows=[r for r in rows if r['start'][:10]>='2022-01-01']
            if tf in ('1-hour','4-hour'):
                # Limited explicit ordinary-session compatibility window. No filling.
                rows=[r for r in rows if '2026-08-17'<=r['start'][:10]<='2026-10-30'
                      and day_record(r['start'][:10])['status']=='REGULAR_SCHEDULED']
                adjusted=[]
                for r in rows:
                    end=min(datetime.fromisoformat(r['start'])+timedelta(hours=1 if tf=='1-hour' else 4),
                            datetime.fromisoformat(r['start'][:10]+'T15:30:00+05:30'))
                    adjusted.append(dict(r,end=end.isoformat()))
                rows=adjusted
            bars=tuple(candle(r) for r in rows if r['quality']=='OHLC_VALID' and candle(r).end_ist<=cutoff)
            if not bars:
                results.append(dict(company_id=cid,symbol=company['symbol'],timeframe=tf,status='NO_DATA_YET'));continue
            try:
                ema10=pine_average((c.close for c in bars),10,'EMA')
                ema20=pine_average((c.close for c in bars),20,'EMA')
                volume=simple_volume_features(bars,cutoff=cutoff,daily=tf=='Daily',weekly=tf=='Weekly',daily_history=daily)
                dtr=(_dtr_arithmetic(bars) if tf in ('Daily','Weekly') else
                     merge_daily_dtr(bars,daily,cutoff=cutoff,timeframe=tf))
                rs=[];dss=[];rs_status='WEEKLY_NOT_IMPLEMENTED_IN_PRESERVED_RS_ENGINE';rs_error=None
                if tf=='Daily':
                    subset=tuple(c for c in bars if benchmark_daily[0].start_ist<=c.start_ist<=benchmark_daily[-1].start_ist)
                    try:
                        rs=calculate_chart_rs(subset,benchmark_daily,cutoff=cutoff,timeframe=tf)
                        rs_status='EXACT_DATED_BENCHMARK_ALIGNMENT'
                    except ValueError as error:
                        rs_status='BLOCKED_EXACT_ALIGNMENT';rs_error=str(error)
                    # Existing daily DSS behavior is preceding20 native daily RVOL.
                    from experiments.earnings_reaction_v1.indicators import pine_rvol
                    dss=pine_rvol((c.volume for c in bars),20)[0]
                elif tf in ('1-hour','4-hour'):
                    days=sorted({c.start_ist.date() for c in bars})
                    sessions=tuple(Session(d,datetime.fromisoformat(str(d)+'T09:15:00+05:30'),
                                           datetime.fromisoformat(str(d)+'T15:30:00+05:30')) for d in days)
                    bminutes=tuple(c for c in benchmark_minutes if c.start_ist.date() in set(days))
                    bench=aggregate(bminutes,sessions,tf,cutoff=cutoff,base_minutes=5)
                    try:
                        rs=calculate_chart_rs(bars,bench,cutoff=cutoff,timeframe=tf)
                        rs_status='EXACT_DATED_BENCHMARK_ALIGNMENT'
                    except ValueError as error:
                        rs_status='BLOCKED_EXACT_ALIGNMENT';rs_error=str(error)
                    dss=dss_rvol_reconstruction(bars,sessions,cutoff=cutoff,bar_minutes=60 if tf=='1-hour' else 240)
                status='PASS_AVAILABLE_IMPLEMENTATIONS'
                results.append(dict(company_id=cid,symbol=company['symbol'],timeframe=tf,status=status,
                    daily_context_from='2022-01-01; bounded compatibility only; full-history seed parity not asserted',
                    bars=len(bars),ema10_rows=len(ema10),ema20_rows=len(ema20),simple_volume_rows=len(volume),
                    dtr_rows=len(bars),dtr_status='PRESERVED_PRIMITIVES; WEEKLY_SOURCE_INTEGRATION_UNCERTIFIED' if tf=='Weekly' else 'EXISTING_ENGINE_REUSED',
                    rs_rows=len(rs),rs_status=rs_status,rs_error=rs_error,dss_rows=len(dss),
                    dss_status='WEEKLY_BEHAVIOR_UNVERIFIED' if tf=='Weekly' else 'EXISTING_RECONSTRUCTION_REUSED',
                    record_history_complete=False,backtest_delivery_verified=False))
                # Feature values remain on Passport, reports retain counts and provenance only.
                q=dict(company_id=cid,instrument_key=company['instrument_key'],timeframe=tf,
                       request_id=sha(f'FEATURES:{cid}:{tf}:{len(bars)}'.encode()))
                target=a.root/'indicator-checks'/str(cid)/(tf+'.json.gz');target.parent.mkdir(parents=True,exist_ok=True)
                payload=dict(ema10=ema10,ema20=ema20,simple_volume=volume,dtr=dtr,rs=rs,dss=dss)
                from .storage import atomic
                a.guard();atomic(target,gzip.compress(json.dumps(payload,default=str).encode(),mtime=0))
            except (ValueError,KeyError) as e:
                results.append(dict(company_id=cid,symbol=company['symbol'],timeframe=tf,status='BLOCKED_EXISTING_ENGINE_CONTRACT',
                    reason=str(e),bars=len(bars)))
    csvout(OUT/'indicator-compatibility.csv',results)
    dump(OUT/'indicator-source-preservation.json',{str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in [
        ROOT/'experiments/earnings_reaction_v1/indicators.py',ROOT/'experiments/earnings_reaction_v1/indicator_alternatives.py',
        ROOT/'experiments/earnings_reaction_v1/simple_volume.py']})
    print('indicator compatibility rows',len(results))

if __name__=='__main__': main()
