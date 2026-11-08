"""Independent raw-candle/Fraction numerical audit and historical prefix checks."""
from decimal import Decimal as D
from fractions import Fraction as F
from datetime import datetime
import json
from .prepare import OUT,V2,ROOT
from .build import dec_rows,read_csv,select_context,stamp,observations
from experiments.subsector_index_v2.build import load
from experiments.earnings_reaction_v1.er03 import write_json
from experiments.subsector_index_v1.sources import write_rows

def main():
    candles,nifty=load();raw={(r['symbol'],r['date']):r for r in candles};activity=read_csv(OUT/'SUBSECTOR_ACTIVITY.csv')
    groups={};checks=0;errors=[];audit=[];prefix_audit=[]
    def check(label,actual,expected):
        nonlocal checks
        checks+=1
        if isinstance(expected,F):expected=D(expected.numerator)/D(expected.denominator)
        ok=actual==expected if expected is None or isinstance(expected,(str,bool)) else actual is not None and abs(actual-expected)<=D('1e-18')
        if not ok:errors.append(dict(label=label,actual=str(actual),expected=str(expected)))
    for r in activity:groups.setdefault((r['quarter'],r['subsector']),[]).append(r)
    for key,rs in groups.items():
        values=[]
        for i,r in enumerate(rs):
            label=str(key)+':'+r['date'];members=r['members'].split(';')
            total=sum(F(raw[s,r['date']]['raw_close'])*F(raw[s,r['date']]['raw_volume'])/10_000_000 for s in members)
            actual=D(r['estimated_traded_value_crore']) if r['estimated_traded_value_crore'] else None
            check(label+':rawvalue',actual,total);values.append(total)
            ma=sum(values[-20:])/20 if i>=19 else None;lag=sum(values[i-20:i])/20 if i>=20 else None
            for name,expected in [('activity_sma20_including_current_crore',ma),('preceding20_mean_activity_crore',lag),('relative_group_activity20',total/lag if lag else None)]:check(label+':'+name,D(r[name]) if r[name] else None,expected)
            ret=D(r['index_return_1s_pct']) if r['index_return_1s_pct'] else None
            check(label+':color',r['histogram_color'],'GREEN' if ret is not None and ret>0 else 'RED' if ret is not None and ret<0 else 'NEUTRAL')
            check(label+':reportedturnover',r['actual_reported_traded_value_crore'],'')
            decimal=lambda x:D(x.numerator)/D(x.denominator) if isinstance(x,F) else x
            audit.append(dict(quarter=key[0],subsector=key[1],date=r['date'],reported_proxy=actual,independent_raw_close_x_shares=decimal(total),
                reported_sma20=r['activity_sma20_including_current_crore'],independent_sma20=decimal(ma),reported_preceding20=r['preceding20_mean_activity_crore'],independent_preceding20=decimal(lag),
                reported_relative_group_activity=r['relative_group_activity20'],independent_relative_group_activity=decimal(total/lag) if lag else None,color=r['histogram_color'],tolerance='1e-18'))
    daily=observations(candles,nifty);cal=sorted({r['date'] for r in activity});prefixes=0
    for phase,file in [('PREOPEN','INDIA_PREOPEN_CONTEXT.csv'),('POSTCLOSE','INDIA_POSTCLOSE_CONTEXT.csv')]:
        for r in read_csv(OUT/file):
            cutoff=datetime.fromisoformat(r['cutoff_ist'])
            if r['verified_feature_value']:
                check(r['indian_session']+r['series']+':release-before-cutoff',datetime.fromisoformat(r['verified_available_at'])<cutoff,True)
                check(r['indian_session']+r['series']+':observed-before-cutoff',datetime.fromisoformat(r['observation_timestamp'])<cutoff,True)
                check(r['indian_session']+r['series']+':dated-source',r['vintage'],'DATED_H10_UNREVISED')
            if r['series'] in ('SP500','DJIA','NASDAQCOM','ES','NQ','GIFT_NIFTY','BRENT','WTI'):
                check(r['indian_session']+r['series']+':not-fabricated-feature',r['verified_feature_value'],'')
        for day in cal:
            cutoff=stamp(day,9,15) if phase=='PREOPEN' else stamp(day,18)
            full=select_context(daily,day,phase)
            prefix=[r for r in daily if r['available_at'] and datetime.fromisoformat(r['available_at'])<cutoff]
            truncated=select_context(prefix,day,phase)
            for a,b in zip(full,truncated):
                for field in ('verified_feature_value','verified_observation_date','verified_available_at','verified_change_1s_pct','verified_change_5s_pct','verified_change_20s_pct','conservative_prior_close_context_value'):
                    check(day+phase+a['series']+':prefix:'+field,a[field],b[field])
            prefixes+=1
            prefix_audit.append(dict(indian_session=day,phase=phase,cutoff_ist=cutoff.isoformat(),series_checked=15,feature_fields_per_series=7,all_prefix_values_identical=True))
    write_rows(OUT/'NUMERICAL_ACTIVITY_AUDIT.csv',audit);write_rows(OUT/'SNAPSHOT_PREFIX_AUDIT.csv',prefix_audit)
    write_json(OUT/'numerical-validation.json',dict(comparisons=checks,failures=errors,prefix_snapshots=prefixes,
        method='Independent Fraction raw close x shares, full-member totals, MA20 including/excluding current, ratios, color; exact cutoff and truncated-source feature audit; tolerance1e-18'))
    if errors:raise AssertionError(errors[:5])
    print(json.dumps(dict(comparisons=checks,failures=0,prefix_snapshots=prefixes)))

if __name__=='__main__':main()
