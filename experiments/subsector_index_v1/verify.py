"""Independent Fraction arithmetic and full-prefix checkpoint audits."""
from fractions import Fraction as F
from decimal import Decimal as D
import json
from .sources import OUT,INPUT,MEMBERS,CUTOFFS,write_rows
from .build import read_prices,calculate,preserve,verify_inputs
from .study import snapshot,CP
from experiments.earnings_reaction_v1.er03 import write_json

def numerical():
    candles=read_prices();stocks,indices,loo,cal,bm=calculate(candles);checks=[]
    def check(series,day,metric,actual,expected):
        if isinstance(expected,F):expected=D(expected.numerator)/D(expected.denominator)
        delta=D(actual)-D(expected) if actual is not None and expected is not None and not isinstance(expected,(bool,str)) else None
        passed=abs(delta)<=D('1e-18') if delta is not None else actual==expected
        checks.append(dict(series=series,date=day,metric=metric,actual=actual,expected=expected,difference=delta,passed=passed))
    # Independent member EMA / lagged-volume equations.
    for s,rows in stocks.items():
        ema={10:None,20:None};vol=[]
        for i,r in enumerate(rows):
            p=F(r['raw_close'])*F(r['share_unit_multiplier']);v=F(r['raw_volume'])/F(r['share_unit_multiplier'])
            for n in ema:
                a=F(2,n+1);ema[n]=p if i==0 else a*p+(1-a)*ema[n]
                check(s,r['date'],f'ema{n}',r[f'ema{n}'],ema[n])
            check(s,r['date'],'rvol20',r['rvol20'],v*20/sum(vol[-20:]) if i>=20 and sum(vol[-20:]) else None)
            check(s,r['date'],'above_ema20',r['above_ema20'],p>ema[20] if i>=19 else None)
            check(s,r['date'],'traded_value_proxy',r['traded_value_proxy_crore'],F(r['raw_close'])*F(r['raw_volume'])/10_000_000)
            vol.append(v)
    for label,rows in [('INDEX:'+q+':'+g,rs) for (q,g),rs in indices.items()]+[('LOO:'+q+':'+s,rs) for (q,s),rs in loo.items()]:
        members=rows[0]['members'].split(';');values=[];level=F(1000);ema={10:None,20:None}
        for i,r in enumerate(rows):
            p=[F(stocks[s][i]['adjusted_close']) for s in members]
            if i:level*=sum(p[j]/F(stocks[s][i-1]['adjusted_close']) for j,s in enumerate(members))/len(members)
            check(label,r['date'],'equal_weight_close',r['index_close'],level)
            values.append(level)
            for n in ema:
                a=F(2,n+1);ema[n]=level if i==0 else a*level+(1-a)*ema[n]
                check(label,r['date'],f'ema{n}',r[f'ema{n}'],ema[n])
            for n in (5,10,20,40):check(label,r['date'],f'return_{n}s_pct',r[f'return_{n}s_pct'],100*(level/values[i-n]-1) if i>=n else None)
            rs=level/values[i-55]/(F(bm[r['date']])/F(bm[cal[i-55]]))-1 if i>=55 else None
            check(label,r['date'],'rs55',r['rs55'],rs)
            if i>=19:
                expected=100*F(sum(stocks[s][i]['above_ema20'] for s in members),len(members))
                check(label,r['date'],'above_ema20_breadth',r['pct_above_ema20'],expected)
            if i>=20:
                expected=100*F(sum(stocks[s][i]['rvol20']>1 for s in members),len(members))
                check(label,r['date'],'rvol_participation',r['pct_rvol_gt1'],expected)
            if 'LOO:' in label:check(label,r['date'],'target_absent',label.split(':')[-1] not in members,True)
    write_rows(OUT/'NUMERICAL_COMPARISONS.csv',checks)
    summary=dict(comparisons=len(checks),failures=sum(not r['passed'] for r in checks),method='Independent exact Fraction daily return chain, EMA, RS55, horizons, RVOL, price-weighted value proxy and breadth; 1e-18 Decimal tolerance',
        note='Corporate action positive fixtures are tested separately; real reviewed share-unit multipliers are 1 throughout this study window.')
    write_json(OUT/'numerical-validation.json',summary)
    if summary['failures']:raise AssertionError(summary)
    return summary

def prefix_audit():
    candles=read_prices();stocks,indices,loo,cal,bm=calculate(candles)
    events=json.loads((INPUT/'EVENTS.json').read_text());checks=[];cache={}
    for event in events:
        for n in CP:
            full=snapshot(event,n,stocks,indices,loo,cal)
            if full['status']!='AVAILABLE_RETROSPECTIVE':continue
            day=full['checkpoint_date'];i=cal.index(day)
            full['nifty_benchmark_close']=bm[day]
            if day not in cache:cache[day]=calculate([r for r in candles if r['date']<=day],cal[:i+1])
            ps,pi,pl,pc,pb=cache[day]
            before=snapshot(event,n,ps,pi,pl,pc)
            before['nifty_benchmark_close']=pb[day]
            if full!=before:raise AssertionError((event['event_id'],n,'Future sensitivity'))
            checks.append(dict(event_id=event['event_id'],checkpoint=n,date=day,all_snapshot_fields_identical=True))
    result=dict(checks=len(checks),failures=0,unique_prefixes=len(cache),method='Rebuild all member features, full indices and leave-one-out indices from candles ending at checkpoint; compare every feature field',rows=checks)
    write_json(OUT/'checkpoint-prefix-validation.json',result)
    return dict(checks=len(checks),failures=0)

def main():
    verify_inputs();print(numerical(),flush=True);print(prefix_audit(),flush=True)
    print(preserve('--compact-review' in __import__('sys').argv))

if __name__=='__main__':main()
