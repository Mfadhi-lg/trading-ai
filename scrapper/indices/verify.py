"""Independent Decimal reconciliation from exported constituent candles.

Does not import the index engine. Does not acquire data or mutate source evidence.
"""
import argparse
from decimal import Decimal as D
import json
from pathlib import Path
from .production import save


def verify(base):
    base=Path(base);read=lambda n:json.loads((base/n).read_text(),parse_float=D)
    prices=read('constituent-candles.json');datasets=read('datasets.json');official=read('official.json')
    benchmark={r['date']:D(r['close']) for r in official['bars']['NSE_INDEX|NIFTY MIDSML 400']}
    examples=[];checks=0
    for code in ('TYRES_REVIEWED_CORE','EMS_REVIEWED_CORE','TRANSFORMERS_REVIEWED_CORE','CARBON_BLACK','PRIVATE_BANKS'):
        data=datasets[code];members=data['members'];maps={s:{r['date']:r for r in prices[s]} for s in members}
        level=D(1000);previous=None;broken=False;history=[];values=[];ema={s:None for s in members};run={s:0 for s in members}
        for i,row in enumerate(data['daily']):
            day=row['date'];parts=[maps[s].get(day) for s in members]
            present=all(p is not None for p in parts)
            if not present:broken=True
            if i and not broken:
                previous_parts=[maps[s].get(previous) for s in members]
                if any(p is None for p in previous_parts):broken=True
                else:level*=sum(D(p['raw_close'])/D(q['raw_close']) for p,q in zip(parts,previous_parts))/len(members)
            expected=None if broken else level
            actual=None if row['index_close'] is None else D(row['index_close'])
            if expected is None:assert actual is None,(code,day,'missing-chain')
            else:assert abs(expected-actual)<D('1e-20'),(code,day,'level',expected,actual)
            checks+=1;history.append(expected)
            for s,p in zip(members,parts):
                if p is None:ema[s]=None;run[s]=0
                else:
                    close=D(p['raw_close']);ema[s]=close if ema[s] is None else D(2)/21*close+D(19)/21*ema[s];run[s]+=1
            value=sum(D(p['raw_close'])*D(p['raw_volume'])/D(10000000) for p in parts) if present else None
            assert (None if row['traded_value_proxy_crore'] is None else D(row['traded_value_proxy_crore']))==value
            checks+=1
            if present and all(run[s]>=20 for s in members):
                above=D(sum(D(p['raw_close'])>ema[s] for s,p in zip(members,parts)))*100/len(members)
                assert abs(D(row['pct_above_ema20'])-above)<D('1e-20');checks+=1
            prior=values[-20:]
            if value is not None and len(prior)==20 and all(x is not None for x in prior) and sum(prior):
                ratio=value/(sum(prior)/20)
                assert abs(D(row['aggregate_activity_ratio20'])-ratio)<D('1e-20');checks+=1
            if i>=55 and expected is not None and history[i-55] is not None and all(r['date'] in benchmark for r in data['daily'][i-55:i+1]):
                ratio=expected/history[i-55]/(benchmark[day]/benchmark[data['daily'][i-55]['date']])-1
                assert abs(D(row['rs55'])-ratio)<D('1e-20');checks+=1
            if day in ('2025-12-02','2026-10-30'):
                examples.append(dict(code=code,date=day,independent_level=expected,stored_level=actual,
                                     independent_value_proxy_crore=value,rs55=row['rs55'],pct_above_ema20=row['pct_above_ema20']))
            values.append(value);previous=day
    result=dict(checks=checks,passed=True,examples=examples,
                basis='Independent Decimal daily 1/N candle ratios, raw-close value proxies, EMA breadth, preceding20 activity and exact56-session RS; selected groups have no confirmed in-window share-unit events in retained reviews.',
                limitation='Source completeness/provider action certification remains unverified; this does not certify historical membership or original delivery.')
    save(base/'independent-verification.json',result)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('build',type=Path);a=p.parse_args();print(verify(a.build)['checks'])
