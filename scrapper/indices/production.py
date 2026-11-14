"""Bounded, reproducible build from retained research and read-only Passport.

No acquisition or runtime activation. Outputs go only to an explicit new directory.
"""
import argparse
from collections import Counter
import csv
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import shutil
from .catalog import research_catalog
from .service import daily, leave_one_out
from experiments.historical_candle_archive_v1.access import HistoricalStore
from experiments.historical_candle_archive_v1.calendar import day_record

ROOT = Path(__file__).resolve().parents[2]


def save(path, value):
    path.write_text(json.dumps(value, default=str, separators=(',', ':'))+'\n')


def ordinary_securities(securities, company_id):
    # Explicit canonical registry links distinguish ordinary shares from rights.
    return {s['isin']:s for s in securities
            if s.get('exchange')=='NSE' and s.get('segment')=='equity'
            and s.get('isin') and s.get('symbol')
            and str(s.get('existing_registry_company_id'))==str(company_id)
            and '-RE' not in s['symbol'].upper()}


def action_inputs(root=ROOT, additional=None):
    files=[root/'artifacts/subsector-index-v1/inputs/corporate-action-review.json',
           root/'artifacts/subsector-index-v2/inputs/peer-action-review.json']
    if additional:files.append(Path(additional))
    reviews=[];events=[]
    for path in files:
        raw=path.read_bytes();review=json.loads(raw)
        reviews.append(dict(source=str(path),sha256=hashlib.sha256(raw).hexdigest(),review=review))
        for key in ('in_window_share_unit_events','ordinary_cash_dividends','confirmed_in_window_events','events'):
            events.extend(review.get(key,[]))
    unique={}
    for event in events:
        key=(event['symbol'],event['ex_date'])
        if key in unique and unique[key]!=event:raise ValueError('Conflicting action evidence')
        unique[key]=event
    return dict(events=list(unique.values()),reviews=reviews,
                completeness='LIMITED_RETAINED_REVIEWS;BROAD_UNIVERSE_AND_LATER_ACTION_COVERAGE_UNCERTIFIED')


def membership(root=ROOT):
    catalog = research_catalog(root/'artifacts/subsector-ss04/research.sqlite3')
    groups = []
    for group in catalog['groups']:
        members=[];unresolved=[]
        for m in group['members']:
            securities=(m.get('security_evidence') or {}).get('securities', [])
            choices=ordinary_securities(securities,m['company_id'])
            if len(choices)!=1:
                unresolved.append(dict(company_id=m['company_id'],reason='UNRESOLVED_NSE_IDENTITY'));continue
            security=next(iter(choices.values()))
            members.append(dict(symbol=security['symbol'],company_id=m['company_id'],
                                instrument_key='NSE_EQ|'+security['isin'],listing_date=security.get('listing_date'),
                                classification=m['classification'],exposures=m['exposures'],security_evidence=security))
        groups.append(dict(code=group['code'],label=group['label'],members=members,unresolved=unresolved,
                           membership_status='VERIFIED_RETROSPECTIVE',source_sha256=catalog['source_sha256']))
    # Existing independently reviewed core sets remain separately identifiable.
    from experiments.subsector_index_v2.build import SETS, PRIMARY, EXTRA_BUSINESS
    from experiments.subsector_index_v1.sources import BUSINESS
    with (root/'artifacts/subsector-index-v2/inputs/registry.csv').open() as stream:
        registry=list(csv.DictReader(stream))
    for code in PRIMARY:
        members=[]
        for symbol in SETS[code]:
            choices=[r for r in registry if r['exchange']=='NSE' and r['symbol']==symbol and r['segment']=='equity']
            if len(choices)!=1:raise ValueError('Ambiguous retained V2 identity '+symbol)
            r=choices[0]
            members.append(dict(symbol=symbol,company_id=int(r['existing_registry_company_id']),
                                instrument_key='NSE_EQ|'+r['isin'],listing_date='2025-11-24' if symbol=='SHILCTECH' else r['listing_date'],
                                classification=dict(role='primary',evidence=EXTRA_BUSINESS.get(symbol,BUSINESS.get(symbol)),known_at='2026-11-04'),security_evidence=r))
        groups.append(dict(code=code+'_REVIEWED_CORE',label=code+' · retained V2 reviewed core',members=members,
                           unresolved=[],membership_status='VERIFIED_RETROSPECTIVE',source='experiments/subsector_index_v2/build.py::SETS'))
    return groups


def build(destination, *, start='2025-12-01', end='2026-11-06', reuse_candles=None, action_review=None,
          incremental_state=None, reviewed_calendar=None):
    destination=Path(destination)
    if destination.exists():raise ValueError('Use a new output directory; retained builds are immutable')
    destination.mkdir(parents=True)
    cutoff=datetime.now(timezone.utc).isoformat()
    groups=membership(); save(destination/'membership.json',groups)
    actions=action_inputs(additional=action_review);save(destination/'action-evidence.json',actions)
    store=HistoricalStore()
    if reviewed_calendar:
        from scrapper.market_refresh.derived import VerifiedStore
        store=VerifiedStore(store, reviewed_calendar)
    try:
        from .official import load_official
        official=load_official(store,start=start,end=end,asof=cutoff,
                               incremental_state=incremental_state,reviewed_calendar=reviewed_calendar)
        save(destination/'official.json',official)
        benchmark_key='NSE_INDEX|NIFTY MIDSML 400'
        benchmark={r['date']:r['close'] for r in official['bars'].get(benchmark_key,[])}
        calendar=[];d=date.fromisoformat(start)
        while str(d)<=end:
            record=reviewed_calendar(d) if reviewed_calendar else day_record(d)
            if reviewed_calendar and record.get('verified') is not True:
                raise ValueError('Unverified constituent calendar window')
            if record['sessions']:calendar.append(str(d))
            d+=timedelta(days=1)
        prices={}; sources={}; coverage=[]; datasets={}
        candidates=[g for g in groups if len(g['members'])>=2 and not g['unresolved']]
        members={m['symbol']:m for g in candidates for m in g['members']}
        if reuse_candles:
            previous=Path(reuse_candles)
            prices=json.loads((previous/'constituent-candles.json').read_text(),parse_float=Decimal)
            sources=json.loads((previous/'sources.json').read_text())
            save(destination/'reused-candles.json',dict(source=str(previous),
                candle_sha256=hashlib.sha256((previous/'constituent-candles.json').read_bytes()).hexdigest(),
                source_sha256=hashlib.sha256((previous/'sources.json').read_bytes()).hexdigest()))
            from .passport import daily_prices
            for symbol in sorted(set(members)-set(prices)):
                m=members[symbol]
                result=daily_prices(store,m['company_id'],symbol,start=start,end=end,decision_time=cutoff)
                if any(r['instrument_key']!=m['instrument_key'] for r in result['rows']):result['rows']=[]
                prices[symbol]=result['rows'];sources[symbol]={k:v for k,v in result.items() if k!='rows'}
        else:
            from .passport import daily_prices
            for i,(symbol,m) in enumerate(sorted(members.items())):
                result=daily_prices(store,m['company_id'],symbol,start=start,end=end,decision_time=cutoff)
                wrong=any(r['instrument_key']!=m['instrument_key'] for r in result['rows'])
                if wrong:
                    result['exclusions'].append(dict(reason='UNRESOLVED_INSTRUMENT_CONTINUITY'))
                    result['rows']=[]
                prices[symbol]=result['rows'];sources[symbol]={k:v for k,v in result.items() if k!='rows'}
                if i%25==0:print('Read daily constituents',i+1,'/',len(members),flush=True)
        for symbol,m in members.items():
            if any(r['instrument_key']!=m['instrument_key'] for r in prices.get(symbol,[])):
                raise ValueError('Reused or acquired instrument continuity mismatch: '+symbol)
            prices[symbol]=[r for r in prices.get(symbol,[]) if start<=r['date']<=end]
        save(destination/'constituent-candles.json',prices);save(destination/'sources.json',sources)
        for g in groups:
            code=g['code'];symbols=[m['symbol'] for m in g['members']]
            record=dict(code=code,members=symbols,expected_constituents=len(symbols),status='UNAVAILABLE')
            if len(symbols)<2 or g['unresolved']:
                record['reason']='INSUFFICIENT_VERIFIED_MEMBERS_OR_UNRESOLVED_IDENTITIES';coverage.append(record);continue
            # Start once all fixed members are listed and have a first observation.
            # Do not drop an established member later, or bridge an interior gap.
            missing=[s for s in symbols if not prices.get(s)]
            if missing:
                record.update(reason='NO_VALID_CANDLES',missing_members=missing);coverage.append(record);continue
            base=max(max(prices[s][0]['date'],next(m.get('listing_date') or start for m in g['members'] if m['symbol']==s)) for s in symbols)
            grid=[d for d in calendar if d>=base]
            if len(grid)<2:
                record['reason']='INSUFFICIENT_SYNCHRONIZED_HISTORY';coverage.append(record);continue
            scoped={s:[r for r in prices[s] if r['date']>=base] for s in symbols}
            listings={m['symbol']:m.get('listing_date') for m in g['members']}
            try:
                rows=daily(scoped,grid,benchmark,members=symbols,listings=listings,actions=actions['events'])
            except ValueError as error:
                record.update(reason=str(error));coverage.append(record);continue
            valid=sum(r['index_close'] is not None for r in rows)
            record.update(status='COMPLETED_PROVISIONAL' if valid==len(grid) else 'PARTIAL' if valid>=2 else 'UNAVAILABLE',
                          valid_levels=valid,expected_bars=len(grid),first_date=grid[0],last_date=grid[-1],
                          last_valid_date=max((r['date'] for r in rows if r['index_close'] is not None),default=None),
                          certification='RETROSPECTIVE_MEMBERSHIP;PROVIDER_ACTION_BASIS_UNCERTIFIED',
                          intraday_status='NOT_BUILT')
            if valid>=2:
                datasets[code]=dict(members=symbols,daily=rows,coverage=record,
                                    leave_one_out=leave_one_out(scoped,grid,benchmark,members=symbols,listings=listings,actions=actions['events']))
            coverage.append(record)
        save(destination/'datasets.json',datasets)
        save(destination/'coverage.json',dict(created_at=cutoff,start=start,end=end,groups=coverage,
                                             counts=dict(Counter(r['status'] for r in coverage))))
        return destination
    finally:
        store.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination',type=Path)
    parser.add_argument('--start',default='2025-12-01');parser.add_argument('--end',default='2026-11-06')
    parser.add_argument('--reuse-candles',type=Path)
    parser.add_argument('--action-review',type=Path)
    args=parser.parse_args();build(args.destination,start=args.start,end=args.end,reuse_candles=args.reuse_candles,action_review=args.action_review)


if __name__=='__main__':main()
