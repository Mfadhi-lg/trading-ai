"""Offline TECH-05 joins. No existing study builder or indicator math is run."""
from datetime import date,datetime,time,timedelta
from decimal import Decimal as D
from collections import defaultdict
from zoneinfo import ZoneInfo
import json
from .prepare import ROOT,OUT,INPUT,V2,MACRO
from experiments.earnings_reaction_v1.er03 import read_csv,digest,write_json
from experiments.subsector_index_v1.sources import write_rows
from experiments.subsector_index_v2.build import load as retained_v2_prices

IST=ZoneInfo('Asia/Kolkata');NY=ZoneInfo('America/New_York')
SERIES=('SP500','NASDAQCOM','DJIA','ES','NQ','NIFTY50','NIFTYMIDSML400','NIFTY_AUTO','NIFTY_CONSUMER_DURABLES','NIFTY_CAPITAL_GOODS','INDIA_VIX','GIFT_NIFTY','USDINR','BRENT','WTI')
INDIAN={'NIFTY50','NIFTYMIDSML400','NIFTY_AUTO','NIFTY_CONSUMER_DURABLES','NIFTY_CAPITAL_GOODS','INDIA_VIX'}
US_HOLIDAYS={date(2025,12,25),date(2026,1,1),date(2026,1,19),date(2026,2,16),date(2026,4,3),date(2026,5,25),date(2026,6,19),date(2026,7,3),date(2026,9,7),date(2026,11,26),date(2026,12,25)}
US_EARLY={date(2025,12,24),date(2026,11,27),date(2026,12,24)}

def stamp(day,h,m=0,tz=IST):return datetime.combine(date.fromisoformat(day) if isinstance(day,str) else day,time(h,m),tz)
def us_completed(cutoff):
    d=cutoff.astimezone(NY).date()
    while True:
        if d.weekday()<5 and d not in US_HOLIDAYS:
            close=stamp(d,13 if d in US_EARLY else 16,tz=NY)
            if close<cutoff:return close
        d-=timedelta(days=1)

def verified_before(r,cutoff):
    if not r.get('decision_time_eligible') or not r.get('available_at') or not r.get('observation_timestamp'):return False
    if datetime.fromisoformat(r['available_at'])>=cutoff or datetime.fromisoformat(r['observation_timestamp'])>=cutoff:return False
    if r['series'] in ('ES','NQ','GIFT_NIFTY'):
        if r.get('observation_kind') not in ('TRADE','QUOTE'):return False
        if not all(r.get(k) for k in ('exchange_contract_id','expiry','roll_policy','provider_entitlement_reference')):return False
    return True

def dec_rows(path):
    rows=read_csv(path)
    for r in rows:
        for k,v in list(r.items()):
            if v and k not in ('date','quarter','subsector','members','missing_members','missing_data_flag','version'):
                try:r[k]=D(v)
                except Exception:pass
    return rows

def activity_rows(indices,breadths,calendar=None):
    grouped=defaultdict(list);out=[]
    for r in indices:grouped[r['quarter'],r['subsector']].append(r)
    bm={(r['quarter'],r['subsector'],r['date']):r for r in breadths}
    calendar=calendar or sorted({r['date'] for r in indices})
    for (q,g),rows in sorted(grouped.items()):
        mapping={r['date']:r for r in rows}
        if len(mapping)!=len(rows):raise ValueError('Duplicate index date')
        template=rows[0]
        rows=[mapping.get(day,dict(template,date=day,observed_constituents=D(0),missing_data_flag='MISSING_SYNCHRONIZED_SESSION',traded_value_proxy_crore=None,
            return_1s_pct='',index_close=None,median_constituent_rvol20='',pct_rvol_gt1='',pct_above_ema20='',pct_rising='')) for day in calendar]
        history=[]
        for i,r in enumerate(rows):
            b=bm.get((q,g,r['date']),r)
            for key in ('traded_value_proxy_crore','expected_constituents','observed_constituents','members'):
                if b[key]!=r[key]:raise ValueError('Breadth/index evidence disagree: '+key)
            complete=r['observed_constituents']==r['expected_constituents'] and not r['missing_data_flag']
            value=r['traded_value_proxy_crore'] if complete else None
            prior=history[-20:];including=(history+[value])[-20:]
            lagged=sum(prior)/20 if len(prior)==20 and all(v is not None for v in prior) else None
            ma=sum(including)/20 if len(including)==20 and all(v is not None for v in including) else None
            change=r['return_1s_pct'] or (D(0) if r['return_1s_pct']==D(0) else None)
            out.append(dict(quarter=q,subsector=g,date=r['date'],estimated_traded_value_crore=value,
                activity_sma20_including_current_crore=ma,preceding20_mean_activity_crore=lagged,
                relative_group_activity20=value/lagged if value is not None and lagged is not None and lagged>0 else None,
                lagged_baseline_first_date=rows[i-20]['date'] if i>=20 else '',lagged_baseline_last_date=rows[i-1]['date'] if i>=20 else '',
                completed_baseline_sessions=len(prior),median_constituent_rvol20=r['median_constituent_rvol20'] if r['median_constituent_rvol20']!='' else None,
                pct_activity_expanding=r['pct_rvol_gt1'] if r['pct_rvol_gt1']!='' else None,
                pct_above_ema20=r['pct_above_ema20'] if r['pct_above_ema20']!='' else None,
                pct_advancing=r['pct_rising'] if r['pct_rising']!='' else None,
                index_close=r['index_close'],index_return_1s_pct=change,
                histogram_color='GREEN' if change is not None and change>0 else 'RED' if change is not None and change<0 else 'NEUTRAL',
                expected_constituents=r['expected_constituents'],observed_constituents=r['observed_constituents'],members=r['members'],
                actual_reported_traded_value_crore=None,reported_value_status='NOT_RETAINED;PROXY_SEPARATE',
                units='INR crore',volume_unit='NSE traded equity shares; no aggregation of share counts',
                calculation='SUM_RAW_CLOSE_INR_TIMES_RAW_SHARES / 10000000;NOT_EXACT_TURNOVER',
                adjustment_policy='Raw close x raw shares; invariant to paired share-unit price/volume normalization. Incomplete corporate-action certification retained.',
                missing_data_flag='' if complete else 'MISSING_FIXED_MEMBER;NO_REWEIGHTING',
                availability='SESSION_CLOSE_DESCRIPTIVE;ACTUAL_HISTORICAL_PROVIDER_DELIVERY_UNATTESTED'))
            history.append(value)
    return out

def changes(rows):
    """Descriptive changes over observations of one source/vintage, never mixed."""
    groups=defaultdict(list)
    for r in rows:groups[r['series'],r['vintage']].append(r)
    for rs in groups.values():
        rs.sort(key=lambda r:r['observation_date']);valid=[]
        for r in rs:
            for n in (1,5,20):
                base=valid[-n] if r['value'] is not None and len(valid)>=n else None
                r[f'change_{n}s_pct']=100*(r['value']/base['value']-1) if base else None
                r[f'change_{n}s_base_date']=base['observation_date'] if base else ''
                r[f'change_{n}s_base_available_at']=base['available_at'] if base else ''
            if r['value'] is not None:valid.append(r)
    return rows

def observations(candles,nifty):
    rows=[];anomalies=[]
    # Reuse the frozen macro rows and verify every retained source body used.
    for r in read_csv(MACRO/'MACRO_DAILY_CONTEXT.csv'):
        if r['series']=='NIFTY50':continue
        p=ROOT/r['source_file']
        if digest(p)!=r['source_sha256']:raise ValueError('Retained macro source changed')
        dated=r['vintage']=='DATED_H10_UNREVISED'
        rows.append(dict(series=r['series'],observation_date=r['observation_date'],value=D(r['value']) if r['value'] else None,
            unit=r['unit'],observation_timestamp=r['observation_timestamp'],original_provider_timestamp=r.get('original_provider_timestamp',''),
            observation_timezone='America/New_York' if dated else 'UNSPECIFIED_DATE_ONLY',
            available_at=r['available_at'],scheduled_release_at=r.get('scheduled_release_at',''),actual_release_at=r.get('actual_publication_timestamp',''),
            availability_basis=r['availability_basis'],decision_time_eligible=dated,provider='Federal Reserve H10' if dated else 'FRED / EIA' if r['series'] in ('BRENT','WTI') else 'FRED / Federal Reserve',
            source_url=r['source_url'],source_file=r['source_file'],source_sha256=r['source_sha256'],retrieved_at=r['retrieved_at'],
            vintage=r['vintage'],date_semantics='DATED_RELEASE_OBSERVATION' if dated else 'DATE_ONLY_CURRENT_VINTAGE',
            status='CONSERVATIVE_DATED_RELEASE_BOUND' if dated else 'RETROSPECTIVE_ONLY',missing_data_flag=r['missing_data_flag'],adjustment=r['adjustment']))
    inventory=json.loads((V2/'inputs/retained-candle-inventory.json').read_text())
    nifty_source=next(r for r in inventory if r['instrument_key']=='NSE_INDEX|Nifty 50')
    tail=json.loads((V2/'nifty-tail-execution.json').read_text())
    def indian(s,day,value,ts,source,sha,receipt='RETAINED_UPSTOX_RECEIPT'):
        return dict(series=s,observation_date=day,value=value,unit='annualized volatility percent' if s=='INDIA_VIX' else 'index points',
            observation_timestamp=stamp(day,15,30).isoformat(),original_provider_timestamp=ts,observation_timezone='Asia/Kolkata',
            available_at=stamp(date.fromisoformat(day)+timedelta(days=1),0).isoformat(),scheduled_release_at='',actual_release_at='',
            availability_basis='CONSERVATIVE_NEXT_MIDNIGHT_BUCKET_BOUND;ACTUAL_DELIVERY_UNATTESTED',decision_time_eligible=False,
            provider='Upstox',source_url='https://upstox.com/developer/api-documentation/v3/get-historical-candle-data/',source_file=source,source_sha256=sha,
            retrieved_at=receipt,vintage='RETAINED_PROVIDER_PRICE_INDEX',date_semantics='INDIAN_SESSION_DATE;CLOSE_TIME_CONCEPTUAL',status='CONCEPTUAL_SESSION_CLOSE_CONTEXT',
            missing_data_flag='',adjustment='PRICE_INDEX;PROVIDER_BASIS_UNATTESTED;VIX_IS_VOLATILITY_NOT_PRICE_RETURN_INDEX')
    for day,value in sorted(nifty.items()):
        source=tail['retained_path'] if day>='2026-08-17' else nifty_source['source'];sha=tail['response_sha256'] if day>='2026-08-17' else nifty_source['sha256']
        rows.append(indian('NIFTY50',day,value,day+'T00:00:00+05:30',source,sha))
    for c in candles:
        if c['symbol']=='NIFTYMIDSML400':rows.append(indian('NIFTYMIDSML400',c['date'],c['raw_close'],c['original_timestamp'],c['original_source_file'],c['original_source_sha256']))
    ledger=OUT/'retrieval-execution.json'
    if ledger.exists():
        log=json.loads(ledger.read_text());plan=json.loads((OUT/'ACQUISITION_PROPOSAL.json').read_text())
        if log['scope_sha256']!=digest(OUT/'ACQUISITION_PROPOSAL.json'):raise ValueError('Acquisition scope mismatch')
        # Any already validated success is usable even if a later request failed.
        for rec in log['requests']:
            if 'retained_path' not in rec:continue
            p=ROOT/rec['retained_path'];spec=plan['requests'][rec['number']-1]
            if rec['instrument_key']!=spec['instrument_key'] or digest(p)!=rec['response_sha256']:raise ValueError('Raw identity/hash mismatch')
            raw=json.loads(p.read_text(),parse_float=D)['data']['candles']
            for b in sorted(raw):
                day=b[0][:10];s=rec['series']
                if not spec['from_date']<=day<=spec['to_date']:raise ValueError('Out-of-range observation')
                if s in INDIAN:rows.append(indian(s,day,D(b[4]),b[0],rec['retained_path'],rec['response_sha256'],rec['received_at']))
                else:
                    d=date.fromisoformat(day);conflict=d.weekday()>4 or d in US_HOLIDAYS
                    if conflict:anomalies.append(dict(series=s,provider_bucket_date=day,original_timestamp=b[0],finding='BUCKET_LABEL_IS_US_NONTRADING_DATE;EXCHANGE_SESSION_DATE_UNRESOLVED',value=D(b[4]),source=rec['retained_path']))
                    rows.append(dict(series=s,observation_date=day,value=D(b[4]),unit='index points',observation_timestamp='',original_provider_timestamp=b[0],
                        observation_timezone='PROVIDER_BUCKET_Asia/Kolkata;EXCHANGE_SESSION_UNRESOLVED',available_at='',scheduled_release_at='',actual_release_at='',
                        availability_basis='UNVERIFIED_EXCHANGE_SESSION_AND_PUBLICATION_TIME',decision_time_eligible=False,provider='Upstox GLOBAL_INDEX',
                        source_url=rec['url'],source_file=rec['retained_path'],source_sha256=rec['response_sha256'],retrieved_at=rec['received_at'],
                        vintage='RETAINED_PROVIDER_CURRENT_HISTORY',date_semantics='PROVIDER_BUCKET_DATE_ONLY;NOT_VERIFIED_US_SESSION_DATE',
                        status='QUARANTINED_FOR_ASOF_USE',missing_data_flag='US_CALENDAR_CONFLICT' if conflict else 'US_SESSION_MAPPING_AND_AVAILABILITY_UNVERIFIED',adjustment='PROVIDER_PRICE_INDEX_CONVENTION_UNATTESTED'))
    write_rows(OUT/'PROVIDER_DATE_ANOMALIES.csv',anomalies)
    return changes(rows)

def select_context(rows,day,phase):
    cutoff=stamp(day,9,15) if phase=='PREOPEN' else stamp(day,18)
    expected=us_completed(cutoff);out=[]
    for s in SERIES:
        pool=[r for r in rows if r['series']==s and r['value'] is not None]
        verified=[r for r in pool if verified_before(r,cutoff)]
        pick=max(verified,key=lambda r:(r['observation_date'],r['available_at'])) if verified else None
        conservative=[r for r in pool if s in INDIAN and r['available_at'] and datetime.fromisoformat(r['available_at'])<cutoff]
        prior=max(conservative,key=lambda r:r['observation_date']) if conservative else None
        same=next((r for r in pool if r['observation_date']==day),None) if s in INDIAN and phase=='POSTCLOSE' else None
        # Non-verified context is separately named. Never attach same-date US
        # daily closes, dated settlements or current-vintage oil to a safe feature.
        retro=[r for r in pool if r['observation_date']<day and not r['decision_time_eligible'] and s not in INDIAN]
        ret=max(retro,key=lambda r:r['observation_date']) if retro else None
        chosen=pick or same or prior or ret
        safechanges={}
        for n in (1,5,20):
            base_available=pick.get(f'change_{n}s_base_available_at') if pick else ''
            safechanges[f'verified_change_{n}s_pct']=pick.get(f'change_{n}s_pct') if pick and base_available and datetime.fromisoformat(base_available)<cutoff else None
        out.append(dict(indian_session=day,phase=phase,cutoff_ist=cutoff.isoformat(),series=s,
            verified_feature_value=pick['value'] if pick else None,safe_under_dated_release_bound=bool(pick),actual_intraday_delivery_attested=False,
            verified_observation_date=pick['observation_date'] if pick else '',verified_available_at=pick['available_at'] if pick else '',
            conservative_prior_close_context_value=prior['value'] if prior else None,prior_close_date=prior['observation_date'] if prior else '',
            same_session_close_context_value=same['value'] if same else None,
            retrospective_value=ret['value'] if ret else None,retrospective_provider_date=ret['observation_date'] if ret else '',
            retrospective_change_1s_pct=ret['change_1s_pct'] if ret else None,
            unit=chosen['unit'] if chosen else 'INR per USD' if s=='USDINR' else 'USD per barrel' if s in ('BRENT','WTI') else 'index points',
            status=pick['status'] if pick else 'CONCEPTUAL_CLOSE_ONLY_NOT_VERIFIED_FEATURE' if prior or same else ret['status'] if ret else 'UNAVAILABLE',
            source_file=chosen['source_file'] if chosen else '',source_sha256=chosen['source_sha256'] if chosen else '',
            retrospective_source_file=ret['source_file'] if ret else '',retrospective_source_sha256=ret['source_sha256'] if ret else '',retrospective_vintage=ret['vintage'] if ret else '',
            prior_close_source_file=prior['source_file'] if prior else '',prior_close_source_sha256=prior['source_sha256'] if prior else '',
            same_session_source_file=same['source_file'] if same else '',same_session_source_sha256=same['source_sha256'] if same else '',
            original_provider_timestamp=chosen['original_provider_timestamp'] if chosen else '',observation_timestamp=chosen['observation_timestamp'] if chosen else '',
            vintage=chosen['vintage'] if chosen else '',availability_basis=chosen['availability_basis'] if chosen else 'NO_TIMESTAMPED_RETAINED_OBSERVATION',
            age_calendar_days=(date.fromisoformat(day)-date.fromisoformat(chosen['observation_date'])).days if chosen else None,
            expected_prior_completed_us_session=expected.date().isoformat(),expected_us_close_ist=expected.astimezone(IST).isoformat(),
            us_calendar_role='CALENDAR_ONLY;NOT_AN_OBSERVATION_OR_RELEASE_ATTESTATION',futures_preopen_quote=None,
            futures_missing='NO_CONTRACT_IDENTIFIED_TIMESTAMPED_INTRADAY_QUOTES' if s in ('ES','NQ','GIFT_NIFTY') else '',**safechanges))
    return out

def sector_rows(indices,daily,nifty):
    by={(r['series'],r['observation_date']):r for r in daily if r['series'] in INDIAN};rows=[]
    for r in indices:
        g=r['subsector'];s='NIFTY_AUTO' if g.startswith('TYRES') else 'NIFTY_CONSUMER_DURABLES' if g.startswith('EMS') else 'NIFTY_CAPITAL_GOODS'
        obs=by.get((s,r['date']));priorlaunch=s=='NIFTY_CAPITAL_GOODS' and r['date']<'2026-06-15'
        rows.append(dict(quarter=r['quarter'],subsector=g,date=r['date'],custom_index_close=r['index_close'],official_series=s,
            official_close=obs['value'] if obs else None,nifty50_close=nifty.get(r['date']),
            official_index_concept='EXTERNALLY_DEFINED_CAPPED_FREE_FLOAT_BROAD_SECTOR',custom_index_concept='FIXED_EQUAL_RETURN_GRANULAR_RESEARCH_SUBSET',
            relevance='Automotive manufacturers, ancillaries and tyres; not a tyre index' if s=='NIFTY_AUTO' else 'Broader consumer-durables contrast; not pure EMS' if s=='NIFTY_CONSUMER_DURABLES' else 'Broad capital goods; not transformer-only',
            launch_date='2026-06-15' if s=='NIFTY_CAPITAL_GOODS' else '2020-01-15' if s=='NIFTY_CONSUMER_DURABLES' else '',
            historical_role='PRE_LAUNCH;BACK_HISTORY_RETROSPECTIVE_ONLY' if priorlaunch else 'POST_LAUNCH',
            status='AVAILABLE_PROVIDER_CLOSE' if obs else 'OFFICIAL_INDEX_EXISTS;NO_VERIFIED_PROVIDER_KEY_OR_RETAINED_HISTORY',
            source_file=obs['source_file'] if obs else 'inputs/source-review.json',source_sha256=obs['source_sha256'] if obs else '',
            identity_warning='Capital Markets index explicitly rejected as a substitute' if s=='NIFTY_CAPITAL_GOODS' else 'Official composition is not custom subsector membership'))
    return rows

def preserve(compact=False):
    before=json.loads((INPUT/'preservation-before.json').read_text());changed=[];missing=[];metadata=[]
    for p,h in before.items():
        if __import__('pathlib').Path(p).name=='.DS_Store':
            if (ROOT/p).exists() and digest(ROOT/p)!=h:metadata.append(p)
            continue
        if not (ROOT/p).exists():missing.append(p)
        elif digest(ROOT/p)!=h:changed.append(p)
    if changed or missing and not compact:raise ValueError('Prior outputs changed: '+str(changed[:5]))
    write_json(OUT/'preservation-verification.json',dict(original_files=len(before),changed=changed,missing_count=len(missing),full_research_preservation_verified=not changed and not missing,
        os_metadata_changes=metadata,os_metadata_policy='Finder .DS_Store excluded from research-file preservation; original baseline retained unchanged',compact=compact))

def main():
    compact='--compact-review' in __import__('sys').argv;preserve(compact)
    indices=dec_rows(V2/'INDEX_V2_DAILY.csv');breadths=dec_rows(V2/'BREADTH_AND_ACTIVITY.csv')
    activity=activity_rows(indices,breadths);write_rows(OUT/'SUBSECTOR_ACTIVITY.csv',activity)
    candles,nifty=retained_v2_prices();daily=observations(candles,nifty);write_rows(OUT/'GLOBAL_MARKET_DAILY.csv',daily)
    calendar=sorted({r['date'] for r in indices});pre=[r for d in calendar for r in select_context(daily,d,'PREOPEN')];post=[r for d in calendar for r in select_context(daily,d,'POSTCLOSE')]
    write_rows(OUT/'INDIA_PREOPEN_CONTEXT.csv',pre);write_rows(OUT/'INDIA_POSTCLOSE_CONTEXT.csv',post)
    sectors=sector_rows(indices,daily,nifty);write_rows(OUT/'NIFTY_SECTOR_BENCHMARKS.csv',sectors)
    inventory=[]
    for s in SERIES:
        rs=[r for r in daily if r['series']==s];valid=[r for r in rs if r['value'] is not None]
        inventory.append(dict(series=s,retained_observations=len(valid),source_null_rows=len(rs)-len(valid),first_provider_date=min((r['observation_date'] for r in valid),default=''),last_provider_date=max((r['observation_date'] for r in valid),default=''),
            integration_status='PARTIAL_RETROSPECTIVE_QUARANTINED' if s in ('SP500','DJIA') and valid else 'INTEGRATED_DAILY_PROVISIONAL' if s in INDIAN and valid else 'PARTIAL_RELEASE_BOUND_AND_RETROSPECTIVE' if s=='USDINR' else 'PARTIAL_RETROSPECTIVE_SPOT' if valid else 'UNAVAILABLE',
            verified_preopen_sessions=sum(r['series']==s and r['safe_under_dated_release_bound'] for r in pre),verified_postclose_sessions=sum(r['series']==s and r['safe_under_dated_release_bound'] for r in post),
            conceptual_prior_close_sessions=sum(r['series']==s and r['conservative_prior_close_context_value'] is not None for r in pre),
            same_session_postclose_context_sessions=sum(r['series']==s and r['same_session_close_context_value'] is not None for r in post),
            providers=';'.join(sorted({r['provider'] for r in rs})),units=';'.join(sorted({r['unit'] for r in rs})),
            limitation='No validated exchange-session/availability mapping' if s in ('SP500','DJIA') else 'No exact provider identity; Tech100 is not Nasdaq Composite' if s=='NASDAQCOM' else 'No contract-specific intraday observations' if s in ('ES','NQ','GIFT_NIFTY') else 'No confirmed Capital Goods provider key; Capital Markets is not equivalent' if s=='NIFTY_CAPITAL_GOODS' else 'Dated weekly noon-NY release is not live Indian FX' if s=='USDINR' else 'Current vintage date-only spot series; not futures settlements' if s in ('BRENT','WTI') else 'Daily close context; historical delivery and complete adjustment certification unattested'))
    write_rows(OUT/'MARKET_DATA_INVENTORY.csv',inventory)
    from .charts import render
    names=render(activity,sectors,pre,post)
    preserve(compact)
    write_json(OUT/'status.json',dict(task='TECH-05',sessions=len(calendar),activity_rows=len(activity),formation_series=12,charts=len(names),context_rows_each=len(pre),
        daily_acceptance='SUPPORTED_DAILY_COMPONENTS_ACCEPTED_WITH_EXPLICIT_LIMITS;VERIFIED_GLOBAL_ASOF_LAYER_INCOMPLETE',
        source_protection='Original V2, ER03C/ER04 and validated indicator code hash-preserved',stop_after_daily_milestone=True))
    print(json.dumps(dict(activity_rows=len(activity),market_rows=len(daily),snapshots=len(pre)+len(post),charts=len(names))))

if __name__=='__main__':main()
