"""Independent Fraction audit of every index and LOO, plus historical prefixes."""
from fractions import Fraction as F
from decimal import Decimal as D
from statistics import median
import json
from .build import load,calculate,OUT,V1,study,read_csv
from experiments.earnings_reaction_v1.er03 import write_json

def main():
    candles,nifty=load();stocks,indices,loo,cal,bm=calculate(candles)
    count=0;failures=[]
    def check(label,day,metric,actual,expected):
        nonlocal count
        count+=1
        if isinstance(expected,F):expected=D(expected.numerator)/D(expected.denominator)
        good=actual==expected if expected is None or isinstance(expected,(str,bool)) else actual is not None and abs(actual-expected)<=D('1e-18')
        if not good:failures.append(dict(series=str(label),date=day,metric=metric,actual=str(actual),expected=str(expected)))
    # Exact independent member recurrences, volume lagging, activity and stock RS55.
    for s,rows in stocks.items():
        ema={10:None,20:None};vol=[];value=[]
        for i,r in enumerate(rows):
            p=F(r['raw_close'])*F(r['share_unit_multiplier']);v=F(r['raw_volume'])/F(r['share_unit_multiplier'])
            for n in ema:
                a=F(2,n+1);ema[n]=p if i==0 else a*p+(1-a)*ema[n]
                check(s,r['date'],'ema'+str(n),r['ema'+str(n)],ema[n])
            check(s,r['date'],'above20',r['above_ema20'],p>ema[20] if i>=19 else None)
            check(s,r['date'],'rvol20',r['rvol20'],v*20/sum(vol[-20:]) if i>=20 and sum(vol[-20:]) else None)
            tv=F(r['raw_close'])*F(r['raw_volume'])/10_000_000
            check(s,r['date'],'value',r['traded_value_proxy_crore'],tv)
            check(s,r['date'],'value_ratio20',r['traded_value_ratio20'],tv*20/sum(value[-20:]) if i>=20 and sum(value[-20:]) else None)
            check(s,r['date'],'stock_rs55',r['rs55'],p/F(rows[i-55]['adjusted_close'])/(F(bm[r['date']])/F(bm[cal[i-55]]))-1 if i>=55 else None)
            vol.append(v);value.append(tv)
    for label,rows in list(indices.items())+list(loo.items()):
        members=rows[0]['members'].split(';');levels=[];level=F(1000);ema={10:None,20:None};rsvalues=[]
        for i,r in enumerate(rows):
            if i:level*=sum(F(stocks[s][i]['adjusted_close'])/F(stocks[s][i-1]['adjusted_close']) for s in members)/len(members)
            check(label,r['date'],'close',r['index_close'],level);levels.append(level)
            check(label,r['date'],'return1',r['return_1s_pct'],100*(level/levels[-2]-1) if i else None)
            for n in ema:
                a=F(2,n+1);ema[n]=level if i==0 else a*level+(1-a)*ema[n]
                check(label,r['date'],'ema'+str(n),r['ema'+str(n)],ema[n])
            for n in (5,10,20,40):check(label,r['date'],'return'+str(n),r[f'return_{n}s_pct'],100*(level/levels[i-n]-1) if i>=n else None)
            rs=level/levels[i-55]/(F(bm[r['date']])/F(bm[cal[i-55]]))-1 if i>=55 else None
            check(label,r['date'],'rs55',r['rs55'],rs);rsvalues.append(rs)
            for n in (1,5):check(label,r['date'],'rs55_change'+str(n),r[f'rs55_change_{n}s'],rs-rsvalues[i-n] if i>=55+n else None)
            for field,key,ready in [('pct_above_ema20','above_ema20',19),('pct_rising','rising',1)]:
                check(label,r['date'],field,r[field],100*F(sum(stocks[s][i][key] for s in members),len(members)) if i>=ready else None)
            check(label,r['date'],'value_sum',r['traded_value_proxy_crore'],sum(F(stocks[s][i]['traded_value_proxy_crore']) for s in members))
            check(label,r['date'],'rvol_breadth',r['pct_rvol_gt1'],100*F(sum(stocks[s][i]['rvol20']>1 for s in members),len(members)) if i>=20 else None)
            check(label,r['date'],'rvol_median',r['median_constituent_rvol20'],median(F(stocks[s][i]['rvol20']) for s in members) if i>=20 else None)
    # Independently audit the additional exported benchmark comparisons and
    # concentration/breadth measurements, not just the reused V1 engine.
    for r in read_csv(OUT/'SUBSECTOR_STRENGTH.csv'):
        i=cal.index(r['date']);members=r['members'].split(';');label=(r['quarter'],r['subsector'])
        for n in (5,10,20,40):
            actual=D(r[f'nifty50_return_{n}s_pct']) if r[f'nifty50_return_{n}s_pct'] else None
            dates=cal[i-n:i+1] if i>=n else []
            expected=100*(F(nifty[r['date']])/F(nifty[dates[0]])-1) if len(dates)==n+1 and all(d in nifty for d in dates) else None
            check(label,r['date'],'nifty50_return'+str(n),actual,expected)
        values=[stocks[s][i]['return_1s_pct'] for s in members]
        check(label,r['date'],'declining_breadth',D(r['pct_declining']) if r['pct_declining'] else None,100*F(sum(v<0 for v in values),len(values)) if i else None)
        absvals=[abs(F(v)) for v in values if v is not None]
        expected=100*max(absvals)/sum(absvals) if absvals and sum(absvals) else None
        check(label,r['date'],'largest_absolute_contribution',D(r['largest_absolute_return_contribution_pct']) if r['largest_absolute_return_contribution_pct'] else None,expected)
    for t in read_csv(OUT/'ER04_FIXED_TRAJECTORIES.csv'):
        if t['status']=='AVAILABLE_RETROSPECTIVE' and t['gain_state_preserved']!='True':raise AssertionError('Unexpected stock outcome revision')
    write_json(OUT/'numerical-validation.json',dict(comparisons=count,failures=failures,method='Independent Fraction chains, EMA, RS55, RS slope, returns, breadth, member RVOL and activity; Decimal tolerance 1e-18'))
    if failures:raise AssertionError(failures[:3])
    # Rebuild all supported checkpoint prefixes, including broad external targets.
    cache={};checks=[]
    for e in json.loads((V1/'inputs/EVENTS.json').read_text()):
        group=study.group_for(e['symbol']);compat={(q,s):rs for (q,g,s),rs in loo.items() if g==group}
        for cp in study.CP:
            full=study.snapshot(e,cp,stocks,indices,compat,cal)
            if full['status']!='AVAILABLE_RETROSPECTIVE':continue
            day=full['checkpoint_date'];i=cal.index(day)
            if day not in cache:cache[day]=calculate([r for r in candles if r['date']<=day],cal[:i+1])
            ps,pi,pl,pc,pb=cache[day];pcmp={(q,s):rs for (q,g,s),rs in pl.items() if g==group}
            prefix=study.snapshot(e,cp,ps,pi,pcmp,pc)
            if full!=prefix:raise AssertionError((e['event_id'],cp,'Future sensitivity'))
            checks.append(dict(event_id=e['event_id'],checkpoint=cp,date=day,identical=True))
    write_json(OUT/'checkpoint-prefix-validation.json',dict(checks=len(checks),failures=0,rows=checks,limit='Verifies causal arithmetic on fixed retrospective sets; does not certify historical member selection'))
    print(json.dumps(dict(numerical_comparisons=count,failures=0,checkpoint_prefixes=len(checks))))

if __name__=='__main__':main()
