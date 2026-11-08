"""Reproducible offline TECH-06 inventories and context; never acquires data.

Run: python3 -m experiments.technical_integration_v2.build
Existing formulas and outputs are read only. New acquisition is a pending plan.
"""
import csv
import gzip
import hashlib
import html
import json
import re
import shutil
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal as D
from pathlib import Path
from zoneinfo import ZoneInfo

from experiments.technical_integration_v1.build import SERIES, INDIAN, US_HOLIDAYS, US_EARLY, stamp, us_completed
from experiments.technical_integration_v1.charts import unpack
from experiments.earnings_reaction_v1.indicators import pine_average
from experiments.earnings_reaction_v1.retrieve_er03 import DailyRequest
from experiments.earnings_reaction_v1.upstox_candles import url_for

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'artifacts/technical-integration-v2'
OLD = ROOT/'artifacts/technical-integration-v1'
IST = ZoneInfo('Asia/Kolkata')
NY = ZoneInfo('America/New_York')
IND_HOLIDAYS=set('2025-12-25 2026-01-15 2026-01-26 2026-03-03 2026-03-26 2026-03-31 2026-04-03 2026-04-14 2026-05-01 2026-05-28 2026-06-26 2026-09-14 2026-10-02 2026-10-20'.split())
IND_SPECIAL={'2026-02-01'}
START, END = '2025-12-01', '2026-10-30'
KEYS = dict(SP500='GLOBAL_INDEX|^GSPC', DJIA='GLOBAL_INDEX|^DJI', GIFT_NIFTY='GLOBAL_INDEX|SGX NIFTY',
            USDINR='GLOBAL_INDICATOR|USDINR', BRENT='GLOBAL_INDICATOR|BZUSD', WTI='GLOBAL_INDICATOR|CLUSD',
            US_TECH100='GLOBAL_INDEX|IXIX', NIFTY50='NSE_INDEX|Nifty 50', NIFTYMIDSML400='NSE_INDEX|NIFTY MIDSML 400',
            INDIA_VIX='NSE_INDEX|India VIX', NIFTY_AUTO='NSE_INDEX|Nifty Auto', NIFTY_CONSUMER_DURABLES='NSE_INDEX|NIFTY CONSR DURBL')
REVERSE = {v:k for k,v in KEYS.items()}
PERIODS = {'FY2026_Q4':('2026-01-01','2026-03-31'), 'FY2027_Q1':('2026-04-01','2026-06-30'),
           'FY2027_Q2':('2026-07-01','2026-09-30')}

def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):
    with Path(p).open(newline='') as f: return list(csv.DictReader(f))
def write(name, rows, fields=None):
    fields=fields or list(dict.fromkeys(k for r in rows for k in r))
    with (OUT/name).open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
def js(name, data): (OUT/name).write_text(json.dumps(data,indent=2,default=str)+'\n')
def norm(s): return re.sub('[^a-z0-9]','',s.lower().replace('&','and'))
def catalog():
    rows=[]
    for n in ('global','nse'):
        p=OUT/'inputs'/f'{n}.json.gz'
        rows += json.loads(gzip.decompress(p.read_bytes()))
    return rows

# Reviewed name aliases link directory names to actual catalog names, never synthesize keys.
ALIASES = {
 'Nifty Financial Services':'Nifty Fin Service','Nifty Financial Services 25/50':'Nifty FinSrv25 50',
 'Nifty Financial Services Ex Bank':'Nifty FinSerExBnk','Nifty Consumer Durables':'NIFTY CONSR DURBL',
 'Nifty Private Bank':'Nifty Pvt Bank','Nifty Oil and Gas':'NIFTY OIL AND GAS',
 'Nifty500 Healthcare':'Nifty500 Health','Nifty MidSmall Healthcare':'Nifty MidSml Hlth',
 'Nifty MidSmall Financial Services':'Nifty MS Fin Serv','Nifty MidSmall IT & Telecom':'Nifty MS IT Telcm',
 'Nifty India Consumption':'Nifty Consumption','Nifty India Digital':'NIFTY IND DIGITAL',
 'Nifty India Manufacturing':'NIFTY INDIA MFG','Nifty India Defence':'Nifty Ind Defence',
 'Nifty India Infrastructure & Logistics':'Nifty InfraLog','Nifty India Railways PSU':'Nifty RailwaysPSU',
 'Nifty India Internet':'Nifty Internet','Nifty India New Age Consumption':'Nifty New Consump',
 'Nifty India Tourism':'Nifty Ind Tourism','Nifty Infrastructure':'Nifty Infra',
 'Nifty EV & New Age Automotive':'Nifty EV','Nifty Core Housing':'Nifty CoreHousing',
 'Nifty Capital Markets':'Nifty Capital Mkt','Nifty Services Sector':'Nifty Serv Sector',
 'Nifty Transportation & Logistics':'Nifty Trans Logis','Nifty MidSmall India Consumption':'Nifty MS Ind Cons',
 'Nifty Non-Cyclical Consumer':'Nifty NonCyc Cons','Nifty100 Enhanced ESG':'Nifty100 Enh ESG',
 'Nifty100 ESG Sector Leaders':'Nifty100ESGSecLdr','Nifty100 Liquid 15':'Nifty100 Liq 15',
 'Nifty Midcap Liquid 15':'Nifty Mid Liq 15','Nifty India Select 5 Corporate Groups (MAATR)':'Nifty Corp MAATR',
 'Nifty India Corporate Group Index - Tata Group 25% Cap':'Nifty Tata 25 Cap',
 'Nifty Conglomerate 50':'NiftyConglomerate','Nifty500 Multicap India Manufacturing 50:30:20':'Nifty Multi Mfg',
 'Nifty500 Multicap Infrastructure 50:30:20':'Nifty Multi Infra',
 'Nifty Microcap 250':'NIFTY MICROCAP250','Nifty Total Market':'NIFTY TOTAL MKT',
 'Nifty MidSmallcap 400':'NIFTY MIDSML 400','Nifty Small Cap 250':'NIFTY SMLCAP 250',
 'Nifty Small Cap 50':'NIFTY SMLCAP 50','Nifty Smallcap 100':'NIFTY SMLCAP 100',
 'Nifty LargeMidcap 250':'NIFTY LARGEMID250','Nifty Midcap Select':'NIFTY MID SELECT',
 'Nifty500 Multicap 50:25:25':'NIFTY500 MULTICAP','Nifty500 LargeMidSmall Equal-Cap Weighted':'Nifty500 LMS Eql',
 'Nifty MidSmallcap400 50:50':'Nifty MidSmall 50 50','Nifty India FPI 150':'Nifty FPI 150',
 'Nifty Smallcap 250':'NIFTY SMLCAP 250','Nifty Smallcap 50':'NIFTY SMLCAP 50',
 'Nifty REITs & Realty':'Nifty REITs Realty',
}

def directories(cat):
    """Discover all entries from the retained official directory sections, not a checklist."""
    text=json.loads((OUT/'inputs/web-directories.json').read_text())
    result=[];used={}
    for title,kind in [('Sectoral Indices','SECTORAL'),('Thematic Indices','THEMATIC'),('Broad based Indices','BROAD_MARKET')]:
        marker='### Broad Based Indices\n' if kind=='BROAD_MARKET' else '## '+title+'\n'
        part=text.split(marker,1)[1].split('### Market Data',1)[0]
        names=re.findall(r'【\d+†([^】]+)】',part)
        for name in names:
            target=ALIASES.get(name,name)
            matches=[r for r in cat if r.get('segment')=='NSE_INDEX' and norm(r['name'])==norm(target)]
            if len(matches)>1: raise ValueError('Ambiguous alias '+name)
            r=matches[0] if matches else None
            if r and r['instrument_key'] in used: raise ValueError('Directory duplicate identity')
            if r: used[r['instrument_key']]=(kind,name)
            result.append(dict(official_name=name,classification=kind,instrument_key=r['instrument_key'] if r else '',
                provider_name=r['name'] if r else '',in_upstox_catalog=bool(r),
                match_basis='REVIEWED_ABBREVIATION_ALIAS' if r and name in ALIASES else 'NORMALIZED_EXACT_NAME' if r else 'NO_CONFIRMED_CATALOG_IDENTITY',
                directory_url='https://www.niftyindices.com/indices/equity/'+{'SECTORAL':'sectoral-indices','THEMATIC':'thematic-indices','BROAD_MARKET':'broad-based-indices'}[kind],
                snapshot_date='2026-11-04',historical_launch_verification='NOT_YET_VERIFIED_PER_INDEX'))
    return result,used

def observations():
    rows=read(OLD/'GLOBAL_MARKET_DAILY.csv')
    for r in rows:
        if sha(ROOT/r['source_file'])!=r['source_sha256']: raise ValueError('Changed retained source')
        r.update(instrument_key=KEYS.get(r['series'],''),actual_vs_estimated='ACTUAL_RETAINED_SOURCE_VALUE' if r['value'] else 'MISSING',
                 revision_status='DATED_UNREVISED_H10_TABLE' if r['vintage']=='DATED_H10_UNREVISED' else 'CURRENT_RETRIEVAL_VINTAGE;CORRECTIONS_NOT_EXCLUDED',
                 historical_available_at_actual=r['actual_release_at'],availability_upper_bound=r['available_at'] if r['vintage']=='DATED_H10_UNREVISED' else '',
                 category='CONSERVATIVE_TIME_BOUNDED' if r['vintage']=='DATED_H10_UNREVISED' else 'RETROSPECTIVE_DESCRIPTIVE',
                 source_observation_timestamp=r['original_provider_timestamp'] if r['series'] in INDIAN else r['observation_timestamp'],
                 observation_timestamp_basis='PROVIDER_CANDLE_START_NOT_FINAL_CLOSE' if r['series'] in INDIAN else 'H10_NOON_NY_FIXING_CONVENTION' if r['vintage']=='DATED_H10_UNREVISED' else r['date_semantics'])
    # A single official public-file diagnostic obtained before Upstox-first correction.
    # No further NSE website harvesting: the terms prohibit automated collection.
    p=OUT/'raw/ind_close_all_30102026.csv'
    if p.exists():
        raw=next(r for r in read(p) if r['Index Name']=='Nifty Capital Goods')
        rows.append(dict(series='NIFTY_CAPITAL_GOODS',observation_date='2026-10-30',value=raw['Closing Index Value'],unit='index points',
            provider='NSE daily index report',source_url='https://archives.nseindia.com/content/indices/ind_close_all_30102026.csv',
            source_file=str(p.relative_to(ROOT)),source_sha256=sha(p),retrieved_at='2026-11-04;EXACT_RECEIPT_NOT_RETAINED',
            original_provider_timestamp='',observation_timestamp='',source_observation_timestamp='',observation_timezone='DATE_ONLY_Asia/Kolkata',
            available_at='',historical_available_at_actual='',availability_upper_bound='',actual_release_at='',
            availability_basis='OFFICIAL_DATE_ONLY_EOD_FILE;HISTORICAL_PUBLICATION_TIME_UNATTESTED',category='RETROSPECTIVE_DESCRIPTIVE',
            revision_status='CURRENT_ARCHIVE_RETRIEVAL;REVISION_HISTORY_UNKNOWN',actual_vs_estimated='ACTUAL_RETAINED_SOURCE_VALUE',
            vintage='OFFICIAL_EOD_ARCHIVE',adjustment='OFFICIAL_PRICE_INDEX;NOT_TOTAL_RETURN',instrument_key='',
            missing_data_flag='SINGLE_DATE_ONLY;FULL_HISTORY_AND_USAGE_PERMISSION_REQUIRED',status='POST_LAUNCH_SINGLE_OFFICIAL_CLOSE',
            observation_timestamp_basis='DATE_ONLY_NO_TIMESTAMP_IN_SOURCE'))
    write('MARKET_DATA_PROVENANCE.csv',rows)
    return rows

def calendars(rows):
    days=sorted({r['indian_session'] for r in read(OLD/'INDIA_PREOPEN_CONTEXT.csv')})
    audit=[]
    d=date.fromisoformat(START)
    while str(d)<=END:
        audit.append(dict(date=str(d),us_cash_session=d.weekday()<5 and d not in US_HOLIDAYS,
            expected_close_ny=stamp(d,13 if d in US_EARLY else 16,tz=NY).isoformat() if d.weekday()<5 and d not in US_HOLIDAYS else '',
            retained_indian_session=str(d) in days,independent_expected_indian_session=(d.weekday()<5 and str(d) not in IND_HOLIDAYS) or str(d) in IND_SPECIAL,
            indian_special='SUNDAY_BUDGET_SESSION' if str(d) in IND_SPECIAL else 'MUNICIPAL_ELECTION_CLOSURE' if str(d)=='2026-01-15' else '',
            basis='NSE_YEARLY_CALENDAR_PLUS_CMTR72260_AND_CMTR72349;NYSE_CALENDAR;NO_PRICE_OR_PUBLICATION_CERTIFICATION'))
        d+=timedelta(days=1)
    write('SESSION_CALENDAR_AUDIT.csv',audit)
    anomalies=[]
    for r in rows:
        if r['series'] not in ('SP500','DJIA'): continue
        d=date.fromisoformat(r['observation_date'])
        anomalies.append(dict(series=r['series'],provider_bucket_date=str(d),original_provider_timestamp=r['original_provider_timestamp'],
            value=r['value'],bucket_is_us_trading_date=d.weekday()<5 and d not in US_HOLIDAYS,
            resolved_us_session_date='',resolution='UNRESOLVED;NO_DATE_SHIFT_APPLIED',source_file=r['source_file'],source_sha256=r['source_sha256']))
    write('UPSTOX_US_SESSION_RECONCILIATION.csv',anomalies)
    return days

def snapshots(rows,days):
    result={}
    for phase in ('PREOPEN','POSTCLOSE'):
        out=[]
        for day in days:
            cutoff=stamp(day,9,15) if phase=='PREOPEN' else stamp(day,18)
            for s in SERIES:
                pool=[r for r in rows if r['series']==s and r.get('value')]
                safe=[r for r in pool if r['category']=='CONSERVATIVE_TIME_BOUNDED' and
                      datetime.fromisoformat(r['availability_upper_bound'])<cutoff and datetime.fromisoformat(r['observation_timestamp'])<cutoff]
                pick=max(safe,key=lambda r:(r['observation_date'],r['availability_upper_bound'])) if safe else None
                retro=[r for r in pool if r['category']=='RETROSPECTIVE_DESCRIPTIVE' and
                       (r['observation_date']<=day if s in INDIAN and phase=='POSTCLOSE' else r['observation_date']<day)]
                ret=max(retro,key=lambda r:r['observation_date']) if retro else None
                chosen=pick or ret
                age=(date.fromisoformat(day)-date.fromisoformat(chosen['observation_date'])).days if chosen else None
                out.append(dict(indian_session=day,phase=phase,cutoff_ist=cutoff.isoformat(),series=s,
                    verified_decision_time_value=None,conservative_time_bounded_value=pick['value'] if pick else None,
                    retrospective_context_value=ret['value'] if ret else None,
                    category=chosen['category'] if chosen else 'UNAVAILABLE',
                    observation_date=chosen['observation_date'] if chosen else '',
                    observation_timestamp=chosen['source_observation_timestamp'] if chosen else '',
                    observation_timestamp_basis=chosen['observation_timestamp_basis'] if chosen else '',
                    availability_upper_bound=pick['availability_upper_bound'] if pick else '',historical_available_at_actual='',
                    availability_basis=chosen['availability_basis'] if chosen else 'NO_RETAINED_SUPPORTED_OBSERVATION',
                    age_calendar_days=age,stale_status='STALE_REFERENCE_OVER_10_CALENDAR_DAYS' if pick and age>10 else 'DATED_REFERENCE_NOT_LIVE' if pick else 'NOT_INTRADAY_FRESHNESS_VERIFIABLE',
                    value_definition='H10_REFERENCE_NOT_FBIL_NOT_LIVE' if pick else chosen.get('adjustment','') if chosen else '',
                    unit=chosen['unit'] if chosen else '',actual_vs_estimated=chosen['actual_vs_estimated'] if chosen else 'MISSING',
                    revision_status=chosen['revision_status'] if chosen else '',missing_data_reason='' if pick else
                    'HISTORICAL_AVAILABILITY_UNATTESTED' if ret else 'NO_AUTHORIZED_ACQUISITION_OR_NO_CONFIRMED_INSTRUMENT',
                    source=chosen['provider'] if chosen else '',source_file=chosen['source_file'] if chosen else '',source_sha256=chosen['source_sha256'] if chosen else '',
                    retrospective_source_file=ret['source_file'] if ret else '',retrospective_source_sha256=ret['source_sha256'] if ret else '',
                    expected_prior_us_session=str(us_completed(cutoff).date()),expected_us_close_ist=us_completed(cutoff).astimezone(IST).isoformat(),
                    exchange_contract_id='',expiry='',observation_kind='',roll_policy='',previous_comparable_value=None,futures_missing=s in ('ES','NQ','GIFT_NIFTY')))
        write(phase+'_MARKET_FEATURES.csv',out);result[phase]=out
    return result

def inventory(cat,dirs,used,rows,days):
    by=defaultdict(list)
    for r in rows:
        if r.get('value') and r.get('instrument_key'): by[r['instrument_key']].append(r)
    retained_intervals=[]
    for spec in json.loads((ROOT/'artifacts/earnings-reaction-v1/er03/market-data-inventory.json').read_text())['datasets']:
        if not spec['instrument_key'].startswith('NSE_INDEX|'):continue
        src=spec['source'];p=ROOT/src['original_path']
        if sha(p)!=src['sha256']:raise ValueError('Retained index interval source changed')
        candles=json.loads(p.read_text())['data']['candles']
        stamps=sorted(c[0] for c in candles)
        retained_intervals.append(dict(instrument_key=spec['instrument_key'],unit=spec['unit'],interval=spec['interval'],
            first_verified_provider_timestamp=stamps[0],last_verified_provider_timestamp=stamps[-1],rows=len(candles),
            source_file=src['original_path'],source_sha256=src['sha256'],
            timestamp_basis='PROVIDER_CANDLE_START;NOT_HISTORICAL_PUBLICATION',decision_time_status='RETROSPECTIVE;RECEIPT_UNATTESTED'))
    write('RETAINED_INDEX_INTERVALS.csv',retained_intervals)
    allrows=[]
    for r in cat:
        if r.get('segment')!='NSE_INDEX' or r.get('instrument_type')!='INDEX': continue
        key=r['instrument_key'];rs=by[key];kind,name=used.get(key,('VOLATILITY' if key==KEYS['INDIA_VIX'] else 'OTHER_UNCLASSIFIED',r['name']))
        allrows.append(dict(official_name=name,provider_name=r['name'],trading_symbol=r['trading_symbol'],instrument_key=key,exchange=r['exchange'],
            classification=kind,snapshot_date='2026-11-04',catalog_source='inputs/nse.json.gz',catalog_sha256=sha(OUT/'inputs/nse.json.gz'),
            current_catalog_present=True,key_resolves_in_retained_response=bool(rs),daily_history_verified=bool(rs),
            retained_daily_observations=len(rs),first_verified_provider_date=min((x['observation_date'] for x in rs),default=''),
            last_verified_provider_date=max((x['observation_date'] for x in rs),default=''),
            intraday_history_verified=any(x['instrument_key']==key and x['unit']=='minutes' for x in retained_intervals),
            intraday_first_timestamp=min((x['first_verified_provider_timestamp'] for x in retained_intervals if x['instrument_key']==key and x['unit']=='minutes'),default=''),
            intraday_last_timestamp=max((x['last_verified_provider_timestamp'] for x in retained_intervals if x['instrument_key']==key and x['unit']=='minutes'),default=''),
            intraday_intervals=';'.join(sorted({str(x['interval'])+' minutes' for x in retained_intervals if x['instrument_key']==key and x['unit']=='minutes'})),prospective_history_verified=False,
            history_status='RETAINED_DAILY_RETROSPECTIVE' if rs else 'NOT_QUERIED;PENDING_AUTHORIZATION',
            launch_date='',prelaunch_status='PER_INDEX_LAUNCH_AND_BACKCALCULATION_REVIEW_REQUIRED',
            **{p+'_price_sessions':sum(a<=x['observation_date']<=b for x in rs) for p,(a,b) in PERIODS.items()},
            **{p+'_full_price_grid':bool(rs) and {d for d in days if a<=d<=b} <= {x['observation_date'] for x in rs} for p,(a,b) in PERIODS.items()},
            source_file=';'.join(sorted({x['source_file'] for x in rs})),missing_data_reason='' if rs else 'NO_EXISTING_AUTHORIZED_HISTORY_REQUEST',
            next_action='Historical delivery/launch attestation' if rs else 'Approve INDEX_ACQUISITION_MANIFEST.json'))
    if len({r['instrument_key'] for r in allrows})!=len(allrows): raise ValueError('Duplicate catalog key')
    write('UPSTOX_ALL_INDICES.csv',allrows)
    coverage=[]
    for r in dirs:
        match=next((x for x in allrows if x['instrument_key']==r['instrument_key']),None)
        coverage.append(dict(r,history_status=match['history_status'] if match else 'ABSENT_FROM_CURRENT_UPSTOX_CATALOG',
            verified_daily_observations=match['retained_daily_observations'] if match else 0,
            launch_date='2026-06-15' if r['official_name']=='Nifty Capital Goods' else '',
            prelaunch_history_role='RETROSPECTIVE_BACKCALCULATION_ONLY' if r['official_name']=='Nifty Capital Goods' else 'UNVERIFIED_DO_NOT_ASSUME_PUBLISHED',
            next_action='Approve bounded Upstox history request' if match and not match['daily_history_verified'] else 'Verify historical publication' if match else 'Provider identity inquiry; do not guess keys'))
    for kind,file in [('SECTORAL','NSE_SECTORAL_INVENTORY.csv'),('THEMATIC','NSE_THEMATIC_INVENTORY.csv'),('BROAD_MARKET','NSE_BROAD_MARKET_INVENTORY.csv')]:
        write(file,[r for r in coverage if r['classification']==kind])
    write('INDEX_HISTORICAL_COVERAGE.csv',allrows)
    return allrows,coverage

def manifests(cat,allrows):
    metadata={str((OUT/'inputs'/n).relative_to(ROOT)):sha(OUT/'inputs'/n) for n in ('global.json.gz','nse.json.gz')}
    requests=[]
    def add(series,key,unit,a,b,reason):
        m=[r for r in cat if r['instrument_key']==key]
        if len(m)!=1: raise ValueError('Unconfirmed identity')
        q=DailyRequest(key,unit,1,date.fromisoformat(a),date.fromisoformat(b))
        requests.append(dict(number=len(requests)+1,series=series,instrument_key=key,unit=unit,interval=1,from_date=a,to_date=b,url=url_for(q),identity=m[0],reason=reason))
    for s in ('USDINR','BRENT','WTI','GIFT_NIFTY','US_TECH100'): add(s,KEYS[s],'days',START,END,'Daily coverage diagnostic; US_TECH100 separate from NASDAQCOM; price definitions unverified')
    for s in ('USDINR','BRENT','WTI','GIFT_NIFTY','US_TECH100','SP500','DJIA'):
        for a,b in [('2026-03-06','2026-03-10'),('2026-06-15','2026-06-19'),('2026-10-26','2026-10-30')]:
            add(s,KEYS[s],'minutes',a,b,'Historical minute capability probe, Q4/Q1 and recent control; not guaranteed supported for global keys')
    for s in ('SP500','DJIA'): add(s,KEYS[s],'minutes','2025-12-22','2025-12-26','US early-close/Christmas bucket diagnostics')
    common=dict(task='TECH-06',status='PENDING_EXPLICIT_MANIFEST_AUTHORIZATION;NOT_EXECUTED',metadata=metadata,
        approved_request_count=0,automatic_retries=0,policy='Reuse existing V3 adapter/token supplier only after explicit manifest approval. Serial >=1s. Retain successful raw bodies and metadata. Stop on first HTTP/schema/empty/transport failure. No live/order endpoints. No credentials read by build.',
        proposed_usage_charge_usd=None,max_authorized_spend_usd=0,cost_basis='Existing Upstox entitlement unknown; no paid upgrade authorized',
        preopen_selection='Inspect bars around 09:15 IST; candle start is not observation/availability time. A bar ending at 09:15 is not strictly-before-cutoff without supporting timestamp evidence.',
        latency_basis='Catalog latency is documented REFRESH INTERVAL, not guaranteed delivery delay; never add it as a historical publication timestamp.')
    js('UPSTOX_DIAGNOSTIC_MANIFEST.json',dict(common,request_count=len(requests),requests=requests))
    requests=[]
    priority={r['instrument_key'] for r in read(OUT/'INDEX_TO_SUBSECTOR_CROSSWALK.csv') if r['q2_focus_priority']=='True' or r['blue_priority']=='True'}
    for r in sorted(allrows,key=lambda r:(r['instrument_key'] not in priority,r['classification']!='SECTORAL',r['classification']!='THEMATIC',r['official_name'])):
        if not r['daily_history_verified']: add(r['official_name'],r['instrument_key'],'days',START,END,'One bounded daily request, reuse all retained history; fiscal Q4/Q1/Q2 and December warmup')
    js('INDEX_ACQUISITION_MANIFEST.json',dict(common,request_count=len(requests),requests=requests,
        warmup_limit='55-session RS55 initially unavailable until 56 consecutive observed sessions; no pre-December requests in this manifest'))
    for n in ('UPSTOX_DIAGNOSTIC_MANIFEST','INDEX_ACQUISITION_MANIFEST'):
        (OUT/(n+'.sha256')).write_text(sha(OUT/(n+'.json'))+'\n')

def rotation(rows,allrows,days,write_output=True,include_all_nse=False):
    by=defaultdict(dict)
    for r in rows:
        if (r['series'] in INDIAN or (include_all_nse and r.get('instrument_key','').startswith('NSE_INDEX|'))) and r.get('value') and r.get('instrument_key'): by[r['instrument_key']][r['observation_date']]=D(r['value'])
    benchmark=by[KEYS['NIFTY50']];out=[]
    for ix in allrows:
        key=ix['instrument_key'];prices=by[key]
        if not prices: continue
        # VIX is a volatility measure and is deliberately not return-ranked with price indices.
        if ix['classification']=='VOLATILITY': continue
        rs=[];ema10=[];ema20=[];run=0;trend=0
        for day in days:
            p=prices.get(day);run=run+1 if p is not None else 0
            # Reuse Pine EMA with explicit reset after gaps; no compressed missing sessions.
            seq=[prices[d] for d in days[len(rs)-run+1:len(rs)+1]] if run else []
            e10=pine_average(seq,10,'EMA')[-1] if seq else None
            e20=pine_average(seq,20,'EMA')[-1] if seq else None
            i=len(rs);r=dict(date=day,instrument_key=key,official_name=ix['official_name'],classification=ix['classification'],close=p,unit='index points',
                ema10=e10,ema20=e20,rs55=None,rs55_change_5s=None,rs55_direction='WARMUP_OR_MISSING',
                relative_strength_vs_nifty50=p/benchmark[day] if p is not None and day in benchmark else None,
                trend_persistence_sessions=0,rank_20s=None,comparable_sector_count=None,
                category='RETROSPECTIVE_DESCRIPTIVE',volume=None,volume_status='OFFICIAL_INDEX_LEVEL_HAS_NO_SHARE_VOLUME',
                missing_data_reason='' if p is not None else 'NO_RETAINED_PRICE',launch_publication_status=ix['prelaunch_status'])
            for n in (1,5,10,20,40):
                base=rs[i-n]['close'] if i>=n and run>n else None
                r[f'return_{n}s_pct']=100*(p/base-1) if p is not None and base is not None else None
            window=days[i-55:i+1] if i>=55 and run>=56 else []
            if len(window)==56 and all(d in benchmark for d in window):
                # Existing SS-INDEX-02 dimensionless ratio-of-returns RS55, not stock pp variant.
                r['rs55']=(p/prices[window[0]])/(benchmark[day]/benchmark[window[0]])-1
            prev=rs[i-5]['rs55'] if i>=5 else None
            if r['rs55'] is not None and prev is not None:
                r['rs55_change_5s']=r['rs55']-prev
                r['rs55_direction']='RISING' if r['rs55_change_5s']>0 else 'FALLING' if r['rs55_change_5s']<0 else 'FLAT'
            direction=1 if p is not None and run>=20 and p>e20 else -1 if p is not None and run>=20 and p<e20 else 0
            trend=trend+direction if direction and (trend>0)==(direction>0) else direction
            r['trend_persistence_sessions']=trend;r['momentum_state']='IMPROVING' if r['rs55_direction']=='RISING' else 'DETERIORATING' if r['rs55_direction']=='FALLING' else 'WARMUP_OR_FLAT'
            r['leadership_state']='EMERGING_LEADERSHIP' if r['rs55'] is not None and r['rs55']>0 and r['rs55_direction']=='RISING' and trend>0 else 'NO_LEADERSHIP_CLAIM'
            rs.append(r)
        out.extend(rs)
    for day in days:
        group=[r for r in out if r['date']==day and r['classification']=='SECTORAL' and r['return_20s_pct'] is not None]
        for r in group:
            r['rank_20s']=1+sum(x['return_20s_pct']>r['return_20s_pct'] for x in group);r['comparable_sector_count']=len(group)
    for key in {r['instrument_key'] for r in out}:
        history=[r for r in out if r['instrument_key']==key]
        for i,r in enumerate(history):
            prev=history[i-5] if i>=5 else None
            comparable=prev and r['rank_20s'] is not None and prev['rank_20s'] is not None and r['comparable_sector_count']==prev['comparable_sector_count']
            r['rank_improvement_5s']=prev['rank_20s']-r['rank_20s'] if comparable else None
            r['possible_rotation']='NEW_LEADER_WITHIN_RETAINED_SECTOR_COHORT' if comparable and r['rank_20s']==1 and prev['rank_20s']>1 else 'NO_NEW_LEADER_IN_RETAINED_COHORT' if comparable else 'WARMUP_OR_NOT_COMPARABLE'
    if write_output:write('NSE_SECTOR_ROTATION_DAILY.csv',out)
    return out

def crosswalk(coverage):
    # This extends analytical links only; retained SS01–SS04 company classifications are unchanged.
    source=ROOT/'artifacts/subsector-index-v2/inputs/classifications.csv'
    rules=[('TYRE',['Nifty Auto']),('TRANSFORMER',['Nifty Capital Goods','Nifty Power','Nifty Infrastructure']),
           ('EMS',['Nifty Consumer Durables','Nifty India Manufacturing']),('ELECTRONIC_MANUFACTUR',['Nifty Consumer Durables','Nifty India Manufacturing']),
           ('API',['Nifty Pharma']),('FORMULATION',['Nifty Pharma']),('CDMO',['Nifty Pharma']),
           ('CHEMICAL',['Nifty Chemicals']),('IT_SERVICES',['Nifty IT']),('BANK',['Nifty Bank','Nifty Financial Services']),
           ('POWER_EQUIPMENT',['Nifty Capital Goods','Nifty Power','Nifty Infrastructure'])]
    focus=read(ROOT/'artifacts/subsector-ss01/v2/q2-deduplicated-focus.csv');focusids={r['company_id'] for r in focus if r['company_id']}
    cohort=read(ROOT/'artifacts/subsector-index-v2/Q2_COHORT_MAPPING.csv')
    blueids={r['company_id'] for r in cohort if r['blue_member']=='True'}
    focusids|={r['company_id'] for r in cohort if r['in_focus']=='True'}
    out=[]
    for r in read(source):
        codes=[r['primary_code']] if r['primary_code'] else []
        try: codes += [x['code'] for x in json.loads(r['exposures_json'])]
        except (ValueError,KeyError): pass
        def matches(token,code):
            if token=='API':return code=='API'
            if token=='FORMULATION':return code in ('FORMULATIONS','ONCOLOGY_FORMULATIONS')
            if token=='CDMO':return code=='PHARMA_CDMO'
            if token=='BANK':return code in ('PRIVATE_BANKS','PUBLIC_BANKS','SMALL_FINANCE_BANKS','PAYMENTS_BANKS')
            return token in code
        names=set(n for code in codes for token,ns in rules if matches(token,code) for n in ns)
        for name in sorted(names) or ['']:
            official=next((x for x in coverage if norm(x['official_name'])==norm(name)),None)
            out.append(dict(company_id=r['company_id'],company_name=r['company_name'],custom_subsector=r['primary_code'],
                exposure_codes=';'.join(sorted(set(codes))),official_index=name,instrument_key=official['instrument_key'] if official else '',
                relationship='CANDIDATE_BROADER_BUSINESS_OVERLAP;NOT_MEMBERSHIP_EQUIVALENCE' if name else 'NO_JUSTIFIED_BENCHMARK_ASSIGNED',
                rationale='Business-activity code maps to broader index mandate; diversified secondary exposures remain candidates' if name else 'No rule forces a broad benchmark',
                classification_status=r['status'],classification_known_at=r['known_at'],historical_availability=r['historical_availability'],
                q2_focus_priority=r['company_id'] in focusids,blue_priority=r['company_id'] in blueids,
                source_file=str(source.relative_to(ROOT)),source_sha256=sha(source)))
    write('INDEX_TO_SUBSECTOR_CROSSWALK.csv',out)
    return out

def global_coverage(cat,rows,snaps):
    out=[]
    for s in SERIES:
        rs=[r for r in rows if r['series']==s and r.get('value')];meta=next((r for r in cat if r['instrument_key']==KEYS.get(s)),None)
        first=min((r['observation_date'] for r in rs),default='');last=max((r['observation_date'] for r in rs),default='')
        blocker='No confirmed exact Upstox instrument; no substitute permitted' if s in ('NASDAQCOM','ES','NQ','NIFTY_CAPITAL_GOODS') else 'New Upstox historical probe requires manifest authorization' if not rs else 'Historical publication/provider delivery unverified'
        out.append(dict(series=s,upstox_instrument_key=KEYS.get(s,''),current_upstox_catalog_present=bool(meta),
            retained_values=len(rs),first_provider_date=first,last_provider_date=last,exact_source=';'.join(sorted({r['provider'] for r in rs})),
            decision_time_status='CONSERVATIVE_DATED_H10_BOUND;NOT_LIVE_FX' if s=='USDINR' else 'NOT_VERIFIED',
            integration_status='REUSED_DAILY_DESCRIPTIVE' if rs else 'UNAVAILABLE',
            verified_preopen_sessions=0,verified_postclose_sessions=0,
            conservative_preopen_sessions=sum(r['series']==s and r['conservative_time_bounded_value'] is not None for r in snaps['PREOPEN']),
            conservative_postclose_sessions=sum(r['series']==s and r['conservative_time_bounded_value'] is not None for r in snaps['POSTCLOSE']),
            blocker=blocker,next_action='Provider identity/definition inquiry' if not meta else 'Approve UPSTOX_DIAGNOSTIC_MANIFEST.json' if s not in INDIAN else 'Historical publication attestation',
            source_files=';'.join(sorted({r['source_file'] for r in rs}))))
    write('GLOBAL_MARKET_COVERAGE.csv',out)
    return out

def futures(days):
    availability=[]
    for s in ('ES','NQ','GIFT_NIFTY'):
        availability.append(dict(series=s,upstox_key=KEYS.get(s,''),actual_contract_verified=False,expiry_verified=False,
            historical_preopen_observations_acquired=0,status='GENERIC_QUOTE_ONLY_NOT_CONTRACT_HISTORY' if s=='GIFT_NIFTY' else 'NO_EXACT_CME_CONTRACT_IN_UPSTOX_CATALOG',
            provider_candidate='NSE IX / NSE Data & Analytics historical files' if s=='GIFT_NIFTY' else 'CME DataMine; Databento GLBX.MDP3',
            coverage='NSE IX advertises historical files; exact study dates and tick coverage need written confirmation' if s=='GIFT_NIFTY' else 'Databento advertises full CME futures history from June 2010; exact requested intervals unqueried',
            cost='EXACT_QUOTE_REQUIRED;NOT_KNOWN;NOT_ZERO',auth_status='NO_CREDENTIAL_OR_PAID_REQUEST_EXECUTED',
            price_definition='REQUIRE_TRADE_OR_BID_ASK;NO_SETTLEMENT_SUBSTITUTION',roll_method='PROPOSED_CALENDAR_LEAD_CONTRACT;VERIFY_DEFINITIONS_AND_SAME_CONTRACT_BASELINE',
            availability_required='Exchange event timestamp and historical capture/receipt timestamp; present-day API retrieval is not historical delivery'))
    write('HISTORICAL_FUTURES_AVAILABILITY.csv',availability)
    windows=[]
    for day in days:
        for phase,h,m in [('PREOPEN',9,15),('POSTCLOSE',18,0)]:
            cutoff=stamp(day,h,m)
            for s in ('ES','NQ','GIFT_NIFTY'):
                windows.append(dict(series=s,indian_session=day,phase=phase,cutoff_ist=cutoff.isoformat(),
                    start_utc=(cutoff-timedelta(minutes=15)).astimezone(ZoneInfo('UTC')).isoformat(),end_exclusive_utc=cutoff.astimezone(ZoneInfo('UTC')).isoformat(),
                    selection='Latest genuine same-contract event with event AND capture time strictly before cutoff; stale if age>60s; retain missing when no event',
                    previous_comparable='Previous Indian-session same-checkpoint same-contract observation; acquire independently even across roll. First checkpoint baseline requires pre-study observation.',
                    contract_id='',expiry='',execution_status='PENDING_ENTITLEMENT_AND_EXPLICIT_AUTHORIZATION'))
    write('FUTURES_REQUEST_WINDOWS.csv',windows)
    js('RESTRICTED_SOURCE_AUTHORIZATION_MANIFEST.json',dict(status='NOT_AUTHORIZED_NOT_EXECUTABLE_UNTIL_PROVIDER_QUOTE_AND_ENTITLEMENT',
        maximum_authorized_spend_usd=0,authenticated_requests_authorized=0,windows_file='FUTURES_REQUEST_WINDOWS.csv',windows_sha256=sha(OUT/'FUTURES_REQUEST_WINDOWS.csv'),
        study_dates=[START,END],indian_checkpoints=len(days),providers=availability,
        us_equity_requirement=dict(series=['SP500','DJIA','NASDAQCOM'],start=START,end=END,
            candidates=['S&P DJI / Nasdaq permission for FRED series','licensed index vendor'],cost=None,
            fields=['actual_session_date','unadjusted_price_index_close','source_publication_timestamp','revision_vintage','research_and_artifact_distribution_rights']),
        capital_goods_requirement=dict(provider='NSE Indices or authorized index-data vendor',start=START,end=END,launch='2026-06-15',cost=None,
            fields=['daily_price_index_close','actual_session_date','historical_publication_basis','launch_vs_backcalculation','permitted_use']),
        contract_roll_policy='Proposed deterministic CME lead-month transitions Dec15 2025, Mar16/Jun15/Sep14 2026 at Globex session opening for those trade dates; definitions must confirm expiry, including Jun18 2026. GIFT nearest eligible unexpired monthly contract per exchange definition; no guessed holiday expiry. No back adjustment; same-contract checkpoint baseline on roll days.',
        estimate_stage=dict(provider='Databento',method='Historical.metadata.get_cost',dataset='GLBX.MDP3',schemas=['definition','trades','mbp-1'],
            stype_in='parent',symbols=['ES.FUT','NQ.FUT'],cost=None,endpoint='https://hist.databento.com/v0/metadata.get_cost',
            request_bounds='Each UTC window in FUTURES_REQUEST_WINDOWS.csv filtered to ES/NQ, plus same-contract previous-checkpoint windows and definition lookup; parent symbols include multiple contracts, filter outrights after definitions',
            status='PROPOSAL_ONLY;ESTIMATE_CALL_REQUIRES_AUTHORIZATION',max_estimate_requests=1,
            estimate_request=dict(dataset='GLBX.MDP3',schema='trades',symbols=['ES.FUT','NQ.FUT'],stype_in='parent',start='2026-03-10T03:30:00Z',end='2026-03-10T03:45:00Z'),
            caution='One-window estimate does not authorize full purchase or establish total price; obtain full-window quote before download')))

def main():
    cat=catalog();dirs,used=directories(cat);rows=observations();days=calendars(rows);snaps=snapshots(rows,days)
    allrows,coverage=inventory(cat,dirs,used,rows,days);crosswalk(coverage);manifests(cat,allrows)
    rotated=rotation(rows,allrows,days);globals_=global_coverage(cat,rows,snaps);futures(days)
    write('NIFTY_SECTOR_RECONCILIATION.csv',coverage)
    from .charts import render
    render(rotated,snaps,allrows)
    from .report import report
    report(allrows,coverage,globals_,rotated,days)
    print(json.dumps(dict(nse_indices=len(allrows),directory_indices=len(coverage),retained_daily_indices=sum(r['daily_history_verified'] for r in allrows),rotation_rows=len(rotated),new_authenticated_requests=0)))

if __name__=='__main__':main()
