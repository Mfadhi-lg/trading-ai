"""Daily equal-weight closing indices. No market acquisition or file mutations.

Forward share-unit normalization is causal: a split/bonus only changes that
date and later prices/volumes. Ordinary dividends are excluded (price index).
Unresolved rights, demergers, missing sessions and missing adjustments never
silently become zero returns or a smaller daily membership.
"""
from decimal import Decimal as D
from datetime import date, timedelta
from statistics import median

LOOKBACKS=(5,10,20,40)

def normalise_prices(rows,actions=()):
    rows=sorted(rows,key=lambda r:r['date'])
    if len({r['date'] for r in rows})!=len(rows):raise ValueError('Duplicate price date')
    events={}
    for a in actions:
        if a['ex_date'] in events:raise ValueError('Ambiguous duplicate corporate action date')
        events[a['ex_date']]=a
    unit=D(1);blocked=False;result=[]
    for raw in rows:
        r=dict(raw);day=r['date'];a=events.get(day)
        if a:
            if a['kind'] in ('SPLIT','BONUS'):
                if a.get('provider_basis')!='UNADJUSTED_CONFIRMED':
                    blocked=True  # avoid double-adjusting an unknown provider basis
                else:
                    factor=D(a['new_shares_per_old_share'])
                    if factor<=0:raise ValueError('Positive share-unit multiplier required')
                    unit*=factor
            elif a['kind']!='ORDINARY_CASH_DIVIDEND':blocked=True
        price=D(r['raw_close']);volume=D(r['raw_volume'])
        if price<=0 or volume<0:raise ValueError('Invalid price/volume')
        r.update(share_unit_multiplier=unit,adjusted_close=price*unit if not blocked else None,
                 comparable_volume=volume/unit if not blocked else None,
                 traded_value_proxy_crore=price*volume/D(10_000_000),
                 adjustment_status='UNRESOLVED_CORPORATE_ACTION' if blocked else 'REVIEWED_NO_IN_WINDOW_SHARE_UNIT_ACTION;FACTOR_1' if unit==1 else 'FORWARD_SPLIT_BONUS_NORMALIZED')
        result.append(r)
    # A known action on a missing observation cannot be silently ignored.
    omitted=[a for day,a in events.items() if rows and rows[0]['date']<=day<=rows[-1]['date'] and day not in {r['date'] for r in rows}]
    if omitted:raise ValueError('Corporate action lacks an ex-date observation')
    return result

def constituent_features(rows,calendar):
    by={r['date']:r for r in rows};out=[];closes=[];volumes=[];values=[];previous=None;run=0
    ema={10:None,20:None}
    for day in calendar:
        raw=by.get(day)
        if raw is None or raw['adjusted_close'] is None:
            out.append(dict(date=day,symbol=rows[0]['symbol'] if rows else '',adjusted_close=None,return_1s_pct=None,
                ema10=None,ema20=None,above_ema20=None,rising=None,rvol20=None,traded_value_proxy_crore=None,traded_value_ratio20=None,
                missing_data_flag='MISSING_PRICE_OR_UNRESOLVED_ACTION'))
            closes=[];volumes=[];values=[];previous=None;run=0;ema={10:None,20:None}
            continue
        r=dict(raw);p=r['adjusted_close'];v=r['comparable_volume'];value=r['traded_value_proxy_crore']
        for n in ema:
            alpha=D(2)/D(n+1);ema[n]=p if ema[n] is None else alpha*p+(1-alpha)*ema[n]
        run+=1
        r.update(return_1s_pct=100*(p/previous-1) if previous is not None else None,
            ema10=ema[10],ema20=ema[20],above_ema20=p>ema[20] if run>=20 else None,
            rising=p>previous if previous is not None else None,
            rvol20=v/(sum(volumes[-20:])/20) if len(volumes)>=20 and sum(volumes[-20:]) else None,
            traded_value_ratio20=value/(sum(values[-20:])/20) if len(values)>=20 and sum(values[-20:]) else None,
            missing_data_flag='')
        out.append(r);previous=p;closes.append(p);volumes.append(v);values.append(value)
    return out

def equal_weight_index(features,calendar,benchmark,*,members,min_members=2):
    """Constant 1/N weight in each daily return; daily close rebalancing.

    All fixed members must have current AND previous synchronized prices.
    A missing return breaks the level chain; resuming needs a new explicit
    series/base, never a retrospective bridge or available-member renormalization.
    """
    members=tuple(sorted(members))
    if len(members)<min_members:return []
    maps={s:{r['date']:r for r in features[s]} for s in members}
    result=[];level=None;broken=False;previous=None;ema={10:None,20:None};run=0
    for i,day in enumerate(calendar):
        parts=[maps[s].get(day) for s in members]
        covered=[p for p in parts if p and p['adjusted_close'] is not None]
        missing=[s for s,p in zip(members,parts) if not p or p['adjusted_close'] is None]
        rets=[p['return_1s_pct'] for p in covered if p['return_1s_pct'] is not None]
        if i==0:
            if len(covered)==len(members):level=D(1000)
            else:broken=True
        elif not broken and len(covered)==len(members) and len(rets)==len(members):
            level*=1+sum(rets)/len(members)/100
        else:broken=True;level=None
        daily=sum(rets)/len(members) if i and len(rets)==len(members) and not broken else None
        if level is not None:
            for n in ema:
                alpha=D(2)/D(n+1);ema[n]=level if ema[n] is None else alpha*level+(1-alpha)*ema[n]
            run+=1
        def breadth(field):
            valid=[p[field] for p in covered if p.get(field) is not None]
            return (100*D(sum(bool(v) for v in valid))/len(valid) if len(valid)==len(members) else None,len(valid))
        above,above_n=breadth('above_ema20');rising,rising_n=breadth('rising')
        rv=[p for p in covered if p['rvol20'] is not None]
        traded=[p['traded_value_ratio20'] for p in covered if p['traded_value_ratio20'] is not None]
        row=dict(date=day,index_close=level,return_1s_pct=daily,ema10=ema[10] if level is not None else None,
            ema20=ema[20] if level is not None else None,ema20_change=ema[20]-previous['ema20'] if level is not None and previous and previous['ema20'] is not None else None,
            rs55=None,rs55_change_1s=None,rs55_change_5s=None,
            pct_above_ema20=above,ema20_breadth_count=above_n,pct_rising=rising,rising_breadth_count=rising_n,
            median_constituent_rvol20=median([p['rvol20'] for p in rv]) if len(rv)==len(members) else None,
            pct_rvol_gt1=100*D(sum(p['rvol20']>1 for p in rv))/len(rv) if len(rv)==len(members) else None,
            pct_rising_and_rvol_gt1=100*D(sum(p['rvol20']>1 and p['rising'] is True for p in rv))/len(rv) if len(rv)==len(members) else None,
            rvol_breadth_count=len(rv),traded_value_proxy_crore=sum(p['traded_value_proxy_crore'] for p in covered) if len(covered)==len(members) else None,
            median_constituent_traded_value_ratio20=median(traded) if len(traded)==len(members) else None,
            expected_constituents=len(members),observed_constituents=len(covered),return_constituents=len(rets),
            price_coverage_pct=100*D(len(covered))/len(members),members=';'.join(members),missing_members=';'.join(missing),
            missing_data_flag='MISSING_CONSTITUENT;CHAIN_BROKEN' if missing else 'EARLIER_MISSING_RETURN;CHAIN_BROKEN' if broken else '',
            rs55_status='WARMUP_OR_MISSING_EXACT_BENCHMARK',available_at=(date.fromisoformat(day[:10])+timedelta(days=1)).isoformat()+'T00:00:00+05:30',
            availability_basis='CONCEPTUAL_SESSION_CLOSE;CONSERVATIVE_PROVIDER_BOUND_NEXT_MIDNIGHT',
            adjustment_certification='SECONDARY_LISTED_ACTION_HISTORY_REVIEW;PRIMARY_COMPLETE_ACTION_FEED_UNAVAILABLE',
            volume_basis='CONSTITUENT_RVOL20_AND_PARTICIPATION_BREADTH;NO_AGGREGATE_SHARE_VOLUME',
            traded_value_basis='SUM_RAW_CLOSE_TIMES_RAW_NSE_VOLUME_DIVIDED_1E7;PROXY_NOT_EXACT_TURNOVER')
        for n in LOOKBACKS:
            base=result[i-n]['index_close'] if i>=n else None
            row[f'return_{n}s_pct']=100*(level/base-1) if level is not None and base is not None else None
        seq=calendar[i-55:i+1] if i>=55 else []
        if level is not None and len(seq)==56 and all(d in benchmark for d in seq) and result[i-55]['index_close'] is not None:
            row['rs55']=level/result[i-55]['index_close']/(benchmark[day]/benchmark[seq[0]])-1
            row['rs55_status']='EXACT_56_OBSERVED_SESSION_RATIO_OF_RETURNS'
        for n in (1,5):
            base=result[i-n]['rs55'] if i>=n else None
            row[f'rs55_change_{n}s']=row['rs55']-base if row['rs55'] is not None and base is not None else None
        result.append(row);previous=row
    return result

def stock_rs(features,calendar,benchmark):
    out=[]
    for i,r in enumerate(features):
        r=dict(r,rs55=None)
        seq=features[i-55:i+1] if i>=55 else []
        if len(seq)==56 and all(x['adjusted_close'] is not None and x['date'] in benchmark for x in seq):
            r['rs55']=r['adjusted_close']/seq[0]['adjusted_close']/(benchmark[r['date']]/benchmark[seq[0]['date']])-1
        out.append(r)
    return out
