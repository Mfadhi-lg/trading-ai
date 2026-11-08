"""Independent TECH-06 numerical, source, cutoff and preservation checks."""
import json
import sys
from collections import Counter,defaultdict
from datetime import date,datetime
from decimal import Decimal as D
from fractions import Fraction as F
from zoneinfo import ZoneInfo
from .build import ROOT,OUT,OLD,read,sha,catalog,KEYS,stamp,us_completed,rotation

def main():
    checks=[]
    def check(name,condition,detail=''):
        checks.append(dict(name=name,passed=bool(condition),detail=detail))
        if not condition: raise AssertionError(name+': '+str(detail))
    ix=read(OUT/'UPSTOX_ALL_INDICES.csv');cat=catalog();nse=[r for r in cat if r.get('segment')=='NSE_INDEX' and r.get('instrument_type')=='INDEX']
    check('All actual NSE_INDEX/INDEX keys retained exactly once',{r['instrument_key'] for r in ix}=={r['instrument_key'] for r in nse} and len(ix)==len(nse)==len({r['instrument_key'] for r in nse}))
    check('Full directory sections retained',len(read(OUT/'NSE_SECTORAL_INVENTORY.csv'))==34 and len(read(OUT/'NSE_THEMATIC_INVENTORY.csv'))==51 and len(read(OUT/'NSE_BROAD_MARKET_INVENTORY.csv'))==22)
    for file,count in [('UPSTOX_DIAGNOSTIC_MANIFEST',28),('INDEX_ACQUISITION_MANIFEST',134)]:
        p=OUT/(file+'.json');m=json.loads(p.read_text());check(file+' hash matches',sha(p)==(OUT/(file+'.sha256')).read_text().strip())
        check(file+' pending, bounded and exact',m['request_count']==count==len(m['requests']) and m['approved_request_count']==0 and m['max_authorized_spend_usd']==0)
        for r in m['requests']:
            check('Manifest identity belongs to freshly downloaded catalog '+str(r['number']),r['identity'] in cat and r['identity']['instrument_key']==r['instrument_key'])
    daily=read(OUT/'MARKET_DATA_PROVENANCE.csv');files={r['source_file']:r['source_sha256'] for r in daily}
    check('Every used raw source hash matches',all(sha(ROOT/p)==h for p,h in files.items()))
    # TECH-05 inherited evidence must remain identical field for field.
    before=read(OLD/'GLOBAL_MARKET_DAILY.csv')
    check('No silent reinterpretation of inherited evidence',all(all(new[k]==v for k,v in old.items()) for old,new in zip(before,daily)) and len(daily)>=len(before))
    cal=read(OUT/'SESSION_CALENDAR_AUDIT.csv')
    check('Independent NSE holiday and special-session grid matches',all(r['retained_indian_session']==r['independent_expected_indian_session'] for r in cal))
    check('Sunday Budget session retained',next(r for r in cal if r['date']=='2026-02-01')['retained_indian_session']=='True')
    check('January election closure retained',next(r for r in cal if r['date']=='2026-01-15')['retained_indian_session']=='False')
    check('US DST March before switch',us_completed(stamp('2026-03-06',9,15)).astimezone(ZoneInfo('Asia/Kolkata')).isoformat()=='2026-03-06T02:30:00+05:30')
    check('US DST March after switch',us_completed(stamp('2026-03-10',9,15)).astimezone(ZoneInfo('Asia/Kolkata')).isoformat()=='2026-03-10T01:30:00+05:30')
    check('US November DST after switch',us_completed(stamp('2026-11-03',9,15)).astimezone(ZoneInfo('Asia/Kolkata')).isoformat()=='2026-11-03T02:30:00+05:30')
    check('US Christmas holiday and early close',us_completed(stamp('2025-12-26',9,15)).astimezone(ZoneInfo('Asia/Kolkata')).isoformat()=='2025-12-24T23:30:00+05:30')
    check('US July observed holiday',str(us_completed(stamp('2026-07-06',9,15)).date())=='2026-07-02')
    a=read(OUT/'UPSTOX_US_SESSION_RECONCILIATION.csv');bad=Counter(r['series'] for r in a if r['bucket_is_us_trading_date']=='False')
    check('Original 9 S&P and 8 Dow anomalies remain unresolved',dict(bad)=={'SP500':9,'DJIA':8} and all(not r['resolved_us_session_date'] for r in a))
    snapshot_checks=0
    for phase in ('PREOPEN','POSTCLOSE'):
        rs=read(OUT/(phase+'_MARKET_FEATURES.csv'));check(phase+' exact independent grid',len(rs)==227*15)
        for r in rs:
            cutoff=datetime.fromisoformat(r['cutoff_ist']);checkvalue=r['conservative_time_bounded_value']
            if r['verified_decision_time_value']: raise AssertionError('Invented verified delivery')
            if checkvalue:
                candidates=[x for x in daily if x['series']==r['series'] and x['category']=='CONSERVATIVE_TIME_BOUNDED' and x['value'] and datetime.fromisoformat(x['availability_upper_bound'])<cutoff and datetime.fromisoformat(x['observation_timestamp'])<cutoff]
                expected=max(candidates,key=lambda x:(x['observation_date'],x['availability_upper_bound']))
                assert r['series']=='USDINR' and r['observation_date']==expected['observation_date'] and D(checkvalue)==D(expected['value'])
                assert datetime.fromisoformat(r['availability_upper_bound'])<cutoff
            if r['series'] in ('SP500','DJIA','NASDAQCOM') and r['observation_date']: assert r['observation_date']<r['indian_session']
            if r['series'] in ('ES','NQ','GIFT_NIFTY'): assert not checkvalue and not r['verified_decision_time_value'] and not r['exchange_contract_id']
            if r['category']=='UNAVAILABLE': assert r['missing_data_reason'] and not r['observation_timestamp']
            snapshot_checks+=1
        check(phase+' no leakage / exact latest release selection',True,str(len(rs))+' rows independently checked')
    check('H10 cutoff distinction retained',next(r for r in read(OUT/'PREOPEN_MARKET_FEATURES.csv') if r['series']=='USDINR' and r['indian_session']=='2026-02-24')['conservative_time_bounded_value']=='' and next(r for r in read(OUT/'POSTCLOSE_MARKET_FEATURES.csv') if r['series']=='USDINR' and r['indian_session']=='2026-02-24')['conservative_time_bounded_value']!='')
    check('Conservative vs strict categories never conflated',all(not r['historical_available_at_actual'] for r in daily if r['category']=='CONSERVATIVE_TIME_BOUNDED'))
    rot=read(OUT/'NSE_SECTOR_ROTATION_DAILY.csv');groups=defaultdict(list);comparisons=0
    for r in rot: groups[r['instrument_key']].append(r)
    bench={r['date']:F(r['close']) for r in groups[KEYS['NIFTY50']]}
    for key,rs in groups.items():
        ema={10:None,20:None}
        for i,r in enumerate(rs):
            p=F(r['close']) if r['close'] else None
            if p is None: ema={10:None,20:None};continue
            for n in ema:
                alpha=F(2,n+1);ema[n]=p if ema[n] is None else alpha*p+(1-alpha)*ema[n]
                assert abs(float(F(r['ema'+str(n)])-ema[n]))<1e-15;comparisons+=1
            for n in (1,5,10,20,40):
                if i>=n:
                    val=100*(p/F(rs[i-n]['close'])-1)
                    assert abs(float(F(r[f'return_{n}s_pct'])-val))<1e-17;comparisons+=1
                else: assert not r[f'return_{n}s_pct']
            if i>=55:
                expected=(p/F(rs[i-55]['close']))/(bench[r['date']]/bench[rs[i-55]['date']])-1
                assert abs(float(F(r['rs55'])-expected))<1e-20;comparisons+=1
            else: assert not r['rs55']
    check('Independent Fraction EMA / exact-session return / RS55 audit',True,str(comparisons)+' comparisons')
    check('No official-index share-volume invention',all(not r['volume'] for r in rot))
    check('No mixed thematic or broad-market sector ranks',all(not r['rank_20s'] for r in rot if r['classification']!='SECTORAL'))
    check('Rank cohort explicitly two retained sectors',all(r['comparable_sector_count'] in ('','2') for r in rot))
    days=sorted({r['date'] for r in rot});baseline_rot=rotation(daily,ix,days,False)
    truncated=rotation([r for r in daily if r['observation_date']<=days[90]],ix,days[:91],False)
    check('Snapshot-prefix transformation has no future dependence',truncated==[r for r in baseline_rot if r['date']<=days[90]])
    perturbed=[dict(r,value=str(D(r['value'])*2)) if r['series']=='NIFTY_AUTO' and r['value'] and r['observation_date']>days[110] else r for r in daily]
    future_rot=rotation(perturbed,ix,days,False)
    check('Future price perturbation leaves earlier rotation features unchanged',[r for r in baseline_rot if r['date']<=days[110]]==[r for r in future_rot if r['date']<=days[110]])
    gapped=rotation([r for r in daily if not (r['series']=='NIFTY_AUTO' and r['observation_date']==days[80])],ix,days,False)
    gaprows=[r for r in gapped if r['instrument_key']==KEYS['NIFTY_AUTO']]
    check('Missing session blocks exact return and RS windows',all(gaprows[i]['return_20s_pct'] is None for i in range(80,101)) and all(gaprows[i]['rs55'] is None for i in range(80,136)) and gaprows[81]['ema20']==gaprows[81]['close'])
    intervals=read(OUT/'RETAINED_INDEX_INTERVALS.csv')
    check('Retained 3900 five-minute benchmark bars preserved',sum(int(r['rows']) for r in intervals if r['unit']=='minutes')==3900 and all(sha(ROOT/r['source_file'])==r['source_sha256'] for r in intervals))
    cross=read(OUT/'INDEX_TO_SUBSECTOR_CROSSWALK.csv')
    check('Unassigned companies remain explicit',any(not r['official_index'] for r in cross))
    check('Multiple broader candidate benchmarks allowed',any(r['official_index']=='Nifty Capital Goods' for r in cross) and any(r['official_index']=='Nifty Power' for r in cross))
    check('No invented Capital Goods key',all(not r['instrument_key'] for r in cross if r['official_index']=='Nifty Capital Goods'))
    check('No pesticide formulation to pharma or banking software to bank lexical collision',all(not (r['official_index']=='Nifty Pharma' and set(r['exposure_codes'].split(';'))=={'PESTICIDE_FORMULATION'}) and not (r['official_index']=='Nifty Bank' and set(r['exposure_codes'].split(';'))=={'BANKING_SOFTWARE'}) for r in cross))
    f=read(OUT/'HISTORICAL_FUTURES_AVAILABILITY.csv');check('Missing contract histories are explicit',len(f)==3 and all(r['historical_preopen_observations_acquired']=='0' and r['actual_contract_verified']=='False' for r in f))
    manifest=json.loads((OUT/'RESTRICTED_SOURCE_AUTHORIZATION_MANIFEST.json').read_text())
    check('Roll proposal preserves June18 expiry exception and contract baseline','Jun18 2026' in manifest['contract_roll_policy'] and 'same-contract' in manifest['contract_roll_policy'])
    baseline=json.loads((OUT/'PRESERVATION_BASELINE.json').read_text());present={p:h for p,h in baseline.items() if (ROOT/p).is_file()}
    changed=[p for p,h in present.items() if sha(ROOT/p)!=h]
    compact='--compact-review' in sys.argv
    check('All included original TECH-05/source/code paths unchanged',not changed,changed)
    check('Full original baseline present unless compact replay',compact or len(present)==len(baseline))
    legacy=json.loads((OLD/'inputs/preservation-before.json').read_text());legacy={p:h for p,h in legacy.items() if p.rsplit('/',1)[-1]!='.DS_Store'}
    legacy_present={p:h for p,h in legacy.items() if (ROOT/p).is_file()}
    check('Inherited TECH-05 full research baseline remains unchanged',all(sha(ROOT/p)==h for p,h in legacy_present.items()) and (compact or len(legacy_present)==len(legacy)),str(len(legacy_present))+' inherited paths verified')
    result=dict(checks=checks,passed=True,numerical_comparisons=comparisons,snapshot_rows_independently_checked=snapshot_checks,
                original_paths_verified=len(present),original_paths_omitted=len(baseline)-len(present),full_workspace_preservation=not compact and len(present)==len(baseline),
                inherited_tech05_research_paths_verified=len(legacy_present),inherited_tech05_research_paths_omitted=len(legacy)-len(legacy_present),
                limitations=['No unavailable historical quote/expiry/receipt/license can be validated by offline tests','Historical launch/publication certification remains unresolved for index histories'])
    (OUT/'validation.json').write_text(json.dumps(result,indent=2)+'\n')
    (OUT/'VALIDATION_REPORT.md').write_text(f'''# TECH-06 validation

Independent offline checks passed. {comparisons} Fraction comparisons verify new price-index EMA/returns/RS55 against independently expressed equations; {snapshot_checks} rows independently checked for strict cutoff selection and category separation. This certifies transformations of retained evidence, not missing historical delivery or index launch histories.

Calendar checks include US spring/fall DST, Christmas early close, observed Independence Day, the Indian 15 January election closure and 1 February Sunday Budget session. The 227-session retained NSE grid matches the independently assembled calendar. Sources: NSE yearly calendar plus [CMTR72260](https://nsearchives.nseindia.com/content/circulars/CMTR72260.pdf) and [CMTR72349](https://nsearchives.nseindia.com/content/circulars/CMTR72349.pdf). Do not mix bank/settlement holidays with equity closures.

All 466 retained US provider buckets audited; 17 holiday anomalies remain unresolved and no dates were shifted. Zero verified historical-delivery feature values were invented. H10 upper bounds stay conservative; oil and Indian closing context stay retrospective. Missing futures have no fabricated contracts, observations, settlements or roll returns. The proposed roll specification explicitly handles June18 2026 and requires same-contract baselines; no actual contract roll series is available to validate.

All raw sources used pass SHA256 validation. All inherited provenance fields are preserved verbatim. {len(present)} original workspace paths match the task-start baseline; {len(baseline)-len(present)} omitted in this execution. The inherited TECH-05 baseline also verifies {len(legacy_present)} research paths unchanged, excluding Finder metadata; {len(legacy)-len(legacy_present)} omitted. New manifests are exact-key bounded proposals only. No credential or authenticated request was used by TECH-06. The public catalogs were retrieved successfully; an earlier NSE public-file probe is separately retained.

The new EMA and return windows require consecutive grid observations. Source-null values remain null. Calendar and source availability are separate. Staleness for H10 is labelled reference age, not live-quote freshness. Global minute timing, futures market schedules, trade/quote definitions, provider corrections and exact paid costs remain unverified acquisition requirements.

Browser results: see browser-validation.json when present. Original nested indicator and subsector fields are checked by chart-preservation.json; no old builder or indicator implementation was changed. Reproducibility/replay evidence is in ZIP_REPLAY.json when present. Test success does not complete the technical block.
''')
    print(json.dumps({k:result[k] for k in ('passed','numerical_comparisons','snapshot_rows_independently_checked','original_paths_verified')}))

if __name__=='__main__':main()
