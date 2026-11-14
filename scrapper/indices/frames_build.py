"""Extend an immutable daily build with validated weekly and owner intraday views."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
from .production import save
from .timeframes import expected_grid, aggregate_weekly, calculate
from experiments.historical_candle_archive_v1.access import HistoricalStore
from experiments.historical_candle_archive_v1.calendar import day_record


def canonical(rows):
    result=[]
    for r in rows:
        sessions=day_record(r['date'])['sessions']
        if not sessions:continue
        result.append(dict(start=sessions[0]['opens_at'],end=sessions[-1]['closes_at'],
                           instrument_key=r.get('instrument_key'),
                           **{k:r.get('raw_'+k,r.get(k,0)) for k in ('open','high','low','close','volume')},
                           source_response_sha256s=r.get('source_response_sha256s',[])))
    return result


def extend(build_path, *, intraday_start='2026-08-17'):
    base=Path(build_path);out=base/'timeframes'
    if out.exists():raise ValueError('Timeframe build already exists; use another daily build')
    out.mkdir()
    read=lambda name:json.loads((base/name).read_text(),parse_float=Decimal)
    daily=read('datasets.json'); prices=read('constituent-candles.json'); coverage=read('coverage.json')
    actions=read('action-evidence.json')['events']
    groups={g['code']:g for g in read('membership.json')}; official=read('official.json')
    start,end=coverage['start'],coverage['end'];cutoff=datetime.now(timezone.utc).isoformat()
    grid=expected_grid(start,end,'Daily',decision_time=cutoff)
    weeks=expected_grid(start,end,'Weekly',decision_time=cutoff)
    benchmark=canonical(official['bars'].get('NSE_INDEX|NIFTY MIDSML 400',[]))
    weekly_benchmark=aggregate_weekly(benchmark,grid,weeks)
    from .benchmark_intraday import load_benchmarks
    intraday_benchmarks=load_benchmarks(intraday_start,end,cutoff)
    save(out/'benchmarks.json',dict(Weekly=weekly_benchmark,**intraday_benchmarks))
    datasets={}; report=[];cache={}
    store=HistoricalStore()
    try:
        for n,(code,data) in enumerate(daily.items()):
            group=groups[code];members=data['members'];base_date=data['coverage']['first_date']
            datasets[code]={};listings={m['symbol']:m.get('listing_date') for m in group['members']}
            for timeframe in ('Weekly','1-hour','4-hour'):
                frame_grid=[g for g in (weeks if timeframe=='Weekly' else expected_grid(max(intraday_start,base_date),end,timeframe,decision_time=cutoff)) if g['start'][:10]>=base_date]
                frame_prices={};issues=[]
                for m in group['members']:
                    symbol=m['symbol']
                    if timeframe=='Weekly':
                        frame_prices[symbol]=aggregate_weekly(canonical(prices[symbol]),grid,frame_grid)
                    else:
                        key=(symbol,timeframe)
                        if key not in cache:
                            result=store.read(m['company_id'],timeframe,start=intraday_start,end=end,decision_time=cutoff,mode='retrospective')
                            wrong=any(r['instrument_key']!=m['instrument_key'] for r in result['rows'])
                            cache[key]=dict(rows=[] if wrong else result['rows'],exclusions=result['exclusions'],
                                            source_revisions=result['source_revisions'],identity_mismatch=wrong)
                        frame_prices[symbol]=cache[key]['rows']
                        issues.extend(dict(symbol=symbol,**r) for r in cache[key]['exclusions'])
                try:
                    rows=calculate(frame_prices,frame_grid,weekly_benchmark if timeframe=='Weekly' else intraday_benchmarks[timeframe],members=members,timeframe=timeframe,decision_time=cutoff,listings=listings,actions=actions)
                except ValueError as e:
                    report.append(dict(code=code,timeframe=timeframe,status='UNAVAILABLE',reason=str(e)));continue
                valid=sum(r['index_close'] is not None for r in rows)
                status='COMPLETED_PROVISIONAL' if rows and valid==len(rows) else 'PARTIAL' if valid>=2 else 'UNAVAILABLE'
                report.append(dict(code=code,timeframe=timeframe,status=status,valid_levels=valid,expected_bars=len(rows),
                                   exclusions=len(issues),last_valid_date=max((r['date'] for r in rows if r['index_close'] is not None),default=None),
                                   benchmark_status='DAILY_DERIVED_WEEKLY' if timeframe=='Weekly' else 'RETAINED_EXACT_5MIN_AGGREGATION;GAPS_REMAIN_EXPLICIT'))
                if valid>=2:
                    loo={}
                    if len(members)>=3:
                        for s in members:
                            loo[s]=calculate(frame_prices,frame_grid,weekly_benchmark if timeframe=='Weekly' else intraday_benchmarks[timeframe],members=[m for m in members if m!=s],timeframe=timeframe,decision_time=cutoff,listings=listings,actions=actions)
                    datasets[code][timeframe]=dict(rows=rows,leave_one_out=loo)
            if n%5==0:print('Built timeframes',n+1,'/',len(daily),flush=True)
        save(out/'datasets.json',datasets);save(out/'coverage.json',report)
        save(out/'intraday-sources.json',cache_to_json(cache))
    finally:store.close()
    return out


def cache_to_json(cache):
    return {s+'|'+t:r for (s,t),r in cache.items()}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('build',type=Path);p.add_argument('--intraday-start',default='2026-08-17')
    a=p.parse_args();extend(a.build,intraday_start=a.intraday_start)
