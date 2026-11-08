"""Run: python3 -B -m experiments.technical_fix_v1.repair

Only writes TECH-FIX output and TECH-06 presentation/status files in this worktree.
Uses retained sources; no credentials, API clients, DBs, or network imports.
"""
import csv, gzip, hashlib, json, re, shutil
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from .policy import inventory, eligible_decision_value, refresh_status
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT/'artifacts/technical-fix-v1'
T = ROOT/'artifacts/technical-integration-v2'
A = ROOT/'artifacts/technical-audit-v1'
N = T/'narrowed-global'
NY, IST = ZoneInfo('America/New_York'), ZoneInfo('Asia/Kolkata')
HOLIDAYS = set('2025-12-25 2026-01-01 2026-01-19 2026-02-16 2026-04-03 2026-05-25 2026-06-19 2026-07-03 2026-09-07 2026-11-26 2026-12-25'.split())
DOC = 'https://upstox.com/developer/api-documentation/announcements/global-instruments/'
CAL = 'https://www.nyse.com/trade/hours-calendars'

def read(p):
    with p.open(newline='') as f: return list(csv.DictReader(f))
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def save(name, obj):
    p=OUT/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(obj,indent=2)+'\n')
def csvout(name, rows):
    fields=list(dict.fromkeys(k for r in rows for k in r))
    with (OUT/name).open('w',newline='') as f:
        w=csv.DictWriter(f,fields);w.writeheader();w.writerows(rows)
def packed(x): return json.dumps(x,separators=(',',':')).replace('</','<\\/')
def jsdata(p, prefix): return json.loads(p.read_text().removeprefix(prefix).strip().removesuffix(';'))
def cash(day): return date.fromisoformat(day).weekday()<5 and day not in HOLIDAYS

def baseline():
    target=OUT/'evidence/BASELINE_HASHES.json'
    if target.exists(): return
    paths=[]
    for folder in ['artifacts/technical-integration-v1','artifacts/technical-integration-v2','artifacts/subsector-index-v1','artifacts/subsector-index-v2','artifacts/technical-audit-v1','experiments/technical_integration_v1','experiments/technical_integration_v2','experiments/subsector_index_v1','experiments/subsector_index_v2','experiments/earnings_reaction_v1']:
        paths.extend(p for p in (ROOT/folder).rglob('*') if p.is_file() and p.suffix not in ('.pyc','.zip'))
    save('evidence/BASELINE_HASHES.json',{str(p.relative_to(ROOT)):sha(p) for p in paths})
    shutil.copy2(T/'TECHNICAL_BLOCK_STATUS.md',OUT/'evidence/TECH06_STATUS_BEFORE.md')
    shutil.copytree(T/'charts',OUT/'evidence/charts-before',ignore=shutil.ignore_patterns('*.js','*.css','assets','stocks','indices'))

def validate_sources():
    # Existing validator verifies exact approved source hashes and retains rejected raw rows.
    # Its output CSVs are deterministic existing integration records; do not run here.
    from experiments.technical_integration_v2.narrowed_validation import validate
    bars=read(N/'VALIDATED_PROVIDER_CANDLES.csv'); raw_sources={}
    for r in bars:
        p=ROOT/r['source_file']; raw_sources[r['source_file']]=r['source_sha256']
    for p,digest in raw_sources.items(): assert sha(ROOT/p)==digest,p
    quarantine=json.loads((N/'QUARANTINED_CANDLES.json').read_text())
    for q in quarantine:
        assert sha(ROOT/q['source_file'])==q['source_sha256']
        raw=json.loads((ROOT/q['source_file']).read_text())['data']['candles']
        assert q['raw_row'] in raw
        assert not any(r['series']==q['series'] and r['unit']=='days' and r['provider_timestamp']==q['raw_row'][0] for r in bars)
    assert {(q['series'],q['raw_row'][0][:10]) for q in quarantine}=={('WTI','2026-06-18'),('GIFT_NIFTY','2026-07-19')}
    for r in bars:
        o,h,l,c=map(float,(r['open'],r['high'],r['low'],r['close']));assert l<=min(o,c)<=max(o,c)<=h
    # Revalidate each exact source and compare the accepted timestamp/price universe.
    groups=defaultdict(list)
    for r in bars: groups[(r['number'],r['source_file'])].append(r)
    checks=[]
    for (num,p), rows in groups.items():
        req=dict(number=num,series=rows[0]['series'],unit=rows[0]['unit'],interval=1,from_date='2025-12-01',to_date='2026-10-30')
        accepted,rejected,stats=validate(json.loads((ROOT/p).read_text())['data']['candles'],req,rows[0]['retrieved_at'])
        assert len(accepted)==len(rows),(p,stats)
        originals={r['provider_timestamp']:r for r in accepted}
        for r in rows:
            for k in ('open','high','low','close'): assert float(r[k])==originals[r['provider_timestamp']][k]
        checks.append(dict(source=p,sha256=sha(ROOT/p),accepted=len(accepted),rejected=len(rejected),result='PASS'))
    csvout('evidence/SOURCE_REVALIDATION.csv',checks)
    save('evidence/QUARANTINED_CANDLES.json',quarantine)
    return bars

def sessions():
    rows=read(T/'UPSTOX_US_SESSION_RECONCILIATION.csv');out=[]
    for r in rows:
        stamp=datetime.fromisoformat(r['original_provider_timestamp']);day=r['provider_bucket_date']
        raw=json.loads((ROOT/r['source_file']).read_text())['data']['candles']
        b=next(b for b in raw if b[0]==r['original_provider_timestamp']);assert float(b[4])==float(r['value'])
        assert sha(ROOT/r['source_file'])==r['source_sha256']
        end=stamp+timedelta(days=1)
        conceptual=datetime.combine(date.fromisoformat(day),time(16),NY) if cash(day) else None
        out.append(dict(**r,open=b[1],high=b[2],low=b[3],close=b[4],original_timestamp_ny=stamp.astimezone(NY).isoformat(),
            official_date_is_cash_session=cash(day),holiday=day in HOLIDAYS,
            conceptual_same_date_close_ny=conceptual.isoformat() if conceptual else '',
            conceptual_same_date_close_ist=conceptual.astimezone(IST).isoformat() if conceptual else '',
            provider_bucket_end_ist=end.isoformat(),
            bucket_end_minus_same_date_close_minutes=(end-conceptual).total_seconds()/60 if conceptual else '',
            verified_cash_session_date='',before='UNRESOLVED; provider observations retained',
            after='CASH_SESSION_INTERPRETATION_QUARANTINED;PROVIDER_OBSERVATION_RETAINED',date_shift_days=0,
            independent_price_status='NO_INDEPENDENT_DATED_PRICE_IDENTIFYING_THIS_PRODUCT_AND_SESSION',
            evidence=CAL+'; '+DOC))
    counts=Counter(r['series'] for r in out if r['holiday']);assert counts=={'DJIA':8,'SP500':9},counts
    csvout('US_SESSION_RECONCILIATION.csv',out)

def definitions():
    catalog=json.loads(gzip.decompress((T/'inputs/global.json.gz').read_bytes()))
    source=read(A/'GLOBAL_DATA_AUDIT.csv');out=[]
    for r in source:
        if r['timeframe']!='days' or not r['instrument_key']:continue
        item=next(x for x in catalog if x['instrument_key']==r['instrument_key'])
        out.append(dict(series=r['series'],instrument_key=r['instrument_key'],provider_name=item['name'],segment=item['segment'],
            product='GENERIC_GIFT_SGX_NIFTY_QUOTATION_NO_VERIFIED_CONTRACT' if r['series']=='GIFT_NIFTY' else 'PROVIDER_GLOBAL_INDEX' if item['segment']=='GLOBAL_INDEX' else 'PROVIDER_ECONOMIC_OR_COMMODITY_INDICATOR',
            currency='UNVERIFIED',denomination='PROVIDER_QUOTE_UNITS_UNVERIFIED',spot_or_future='UNVERIFIED',
            official_benchmark_identity='NOT_NASDAQ_COMPOSITE;EXACT_BENCHMARK_UNVERIFIED' if r['series']=='US_TECH100' else 'PARTIAL',
            timestamp='API candle start; observed daily midnight IST; global session aggregation convention unconfirmed',
            historical_revision_policy='NOT_ESTABLISHED_BY_REVIEWED_OFFICIAL_DOCUMENTATION',
            refresh_interval=item.get('latency',''),refresh_is_delivery_evidence=False,
            provider_hours_ist=str(item.get('start_time',''))+' to '+str(item.get('end_time','')),
            historical_decision_time_status='RETROSPECTIVE_ONLY',identity_verification='PARTIAL',
            missing_evidence='Underlying quote/feed origin; currency and unit attestation; fixing or settlement convention; global daily session mapping; correction/vintage policy; historical publication and delivery',
            retained_catalog_sha256=sha(T/'inputs/global.json.gz'),official_documentation=DOC,source_files=r['retained_source_files']))
    csvout('GLOBAL_INSTRUMENT_DEFINITIONS.csv',out)

def nse():
    rows=read(A/'NSE_INDEX_COVERAGE.csv'); catalog=read(T/'UPSTOX_ALL_INDICES.csv');known={r['instrument_key']:r for r in catalog}
    actual_counts={}
    for key, item in known.items():
        stamps={}
        for source in item['source_file'].split(';'):
            if not source:continue
            for b in json.loads((ROOT/source).read_text())['data']['candles']:
                if '2025-12-01'<=b[0][:10]<='2026-10-30':stamps[b[0]]=b
        actual_counts[key]=len(stamps)
        # Nifty50 retains earlier warmup; exported count includes the full source range.
        full={}
        for source in item['source_file'].split(';'):
            if source:
                for b in json.loads((ROOT/source).read_text())['data']['candles']:full[b[0]]=b
        assert len(full)==int(item['retained_daily_observations']),(key,len(full),item['retained_daily_observations'])
    cross=read(T/'INDEX_TO_SUBSECTOR_CROSSWALK.csv');priority=Counter(r['instrument_key'] for r in cross if r['instrument_key'] and (r['q2_focus_priority']=='True' or r['blue_priority']=='True'))
    focus=Counter(r['instrument_key'] for r in cross if r['instrument_key'] and r['q2_focus_priority']=='True')
    blue=Counter(r['instrument_key'] for r in cross if r['instrument_key'] and r['blue_priority']=='True')
    for r in rows:
        r.update(daily_history_exists=int(r.get('actual_daily_observations') or 0)>0,
            historical_availability='RETAINED_RETROSPECTIVE_ONLY' if int(r.get('actual_daily_observations') or 0)>0 else 'NOT_ACQUIRED',
            older_history_backcalculated=('NO_RETAINED_PRELAUNCH_CANDLES' if r.get('launch_date') and r.get('first') and r['first'][:10]>=r['launch_date'] else 'UNKNOWN_REQUIRES_OFFICIAL_METHODOLOGY_AND_VINTAGE'),
            source_verification_status='PARTIAL' if int(r.get('actual_daily_observations') or 0)>0 else 'UNVERIFIED_HISTORY',
            focus_mapped_companies=focus[r['instrument_key']],blue_mapped_companies=blue[r['instrument_key']],
            canonical_identity_status=r['catalog_identity'],study_window_raw_observations=actual_counts.get(r['instrument_key'],0))
        if r['instrument_key'] in known:
            assert int(r.get('actual_daily_observations') or 0)==int(known[r['instrument_key']]['retained_daily_observations'])
    assert len(known)==139 and sum(r['daily_history_exists'] for r in rows if r['instrument_key'] in known)==5
    csvout('NSE_INDEX_COVERAGE.csv',rows)
    manifest=json.loads((T/'INDEX_ACQUISITION_MANIFEST.json').read_text())
    selected=[r for r in manifest['requests'] if priority[r['instrument_key']]]
    selected.sort(key=lambda r:(-priority[r['instrument_key']],r['instrument_key']))
    for r in selected:
        r['priority_basis']=dict(focus_company_count=focus[r['instrument_key']],blue_company_count=blue[r['instrument_key']])
    assert len(selected)==7
    save('PRIORITY_NSE_ACQUISITION_MANIFEST.json',dict(task='TECH-FIX-01',authorized=False,executed_requests=0,request_count=len(selected),
        max_requests=len(selected),automatic_retries=0,max_authorized_spend_usd=0,
        from_date='2025-12-01',to_date='2026-10-30',
        policy='Requires explicit authorization; serial >=1s; stop on first failure; no substitutions, retries or expansion. Reuse Auto and Consumer Durables unchanged. Complete launch/backcast verification before integrating returned history.',requests=selected))

def corporate():
    rows=read(ROOT/'artifacts/subsector-index-v2/CONSTITUENT_DAILY_FEATURES.csv'); groups=defaultdict(list)
    for r in rows: groups[r['symbol']].append(r)
    elig={r['symbol']:r for r in read(ROOT/'artifacts/subsector-index-v2/HISTORICAL_ELIGIBILITY.csv')}
    evidence=json.loads((ROOT/'artifacts/subsector-index-v2/inputs/peer-action-review.json').read_text());actions={r['symbol']:r for r in evidence['members']}
    v1=json.loads((ROOT/'artifacts/subsector-index-v1/inputs/corporate-action-review.json').read_text())
    v1actions={r['symbol']:r for r in v1['action_review']}
    result=[]; jumps=[]
    for symbol, seq in sorted(groups.items()):
        seq.sort(key=lambda r:r['date']); unique={r['date']:r for r in seq};seq=list(unique.values()); previous=None;changes=[]
        for r in seq:
            # independently read and verify each linked original close; duplicates across formations collapse by date.
            p=ROOT/r['original_source_file'];assert sha(p)==r['original_source_sha256']
            if previous:
                change=100*(float(r['raw_close'])/float(previous['raw_close'])-1);changes.append((abs(change),r['date'],change))
                if abs(change)>=20:jumps.append(dict(symbol=symbol,date=r['date'],previous_date=previous['date'],previous_close=previous['raw_close'],close=r['raw_close'],change_pct=change,status='REVIEW_CANDIDATE_NOT_ACTION_FACTOR',source=r['original_source_file']))
            previous=r
        largest=max(changes,default=(0,'',0));e=elig.get(symbol,{})
        result.append(dict(symbol=symbol,instrument_key=seq[0]['instrument_key'],first=seq[0]['date'],last=seq[-1]['date'],observations=len(seq),
            listing_date=e.get('listing_date',''),same_isin_pre_and_post_cutoff=e.get('same_isin_pre_and_post_cutoff',''),
            suspension_status=e.get('suspension_limit',''),action_evidence=json.dumps(dict(v1=v1actions.get(symbol,{}),v2=actions.get(symbol,{})),sort_keys=True),
            known_prebase_actions=json.dumps([r for r in v1['prior_known_share_unit_events'] if r['symbol']==symbol]),
            prebase_action_policy='NO_REAPPLICATION_AFTER_STUDY_BASE',ordinary_dividend_policy='PRICE_INDEX;NO_REINVESTMENT',
            max_abs_close_change_pct=largest[0],max_change_date=largest[1],signed_change_pct=largest[2],
            split_bonus_rights_demerger_archive='INCOMPLETE',provider_adjustment_convention='UNATTESTED',
            share_unit_factor='1_PROVISIONAL_UNCHANGED',repair='NONE_NO_AUTHORITATIVE_FACTOR_ESTABLISHED',status='RETROSPECTIVE_ONLY',
            missing_evidence='Complete primary ex-date action and issuer-chain/security/suspension archive; adjusted/unadjusted provider attestation'))
    csvout('CORPORATE_ACTION_GAPS.csv',result)
    csvout('evidence/DISCONTINUITY_CANDIDATES.csv',jumps or [dict(status='NO_ABSOLUTE_CLOSE_CHANGE_GE_20_PERCENT;NOT_PROOF_OF_NO_ACTIONS')])

def timing():
    rows=read(N/'INDIAN_CHECKPOINT_CONTEXT.csv')
    for name in ('PREOPEN_MARKET_FEATURES.csv','POSTCLOSE_MARKET_FEATURES.csv'):
        for r in read(T/name):
            r.update(channel='LEGACY_DISTINCT_SOURCE',retrospective_value=r.get('retrospective_context_value',''),
                verified_value=r.get('verified_decision_time_value',''),
                historical_available_at=r.get('historical_available_at_actual',''))
            rows.append(r)
    out=[]
    for r in rows:
        if not r.get('cutoff_ist'):continue
        r['acceptance_state']=r.get('category','UNAVAILABLE')
        if r['acceptance_state'] not in ('VERIFIED_DECISION_TIME','CONSERVATIVE_TIME_BOUNDED','RETROSPECTIVE_ONLY','UNAVAILABLE'):
            r['acceptance_state']='CONSERVATIVE_TIME_BOUNDED' if r.get('conservative_time_bounded_value') else 'RETROSPECTIVE_ONLY' if r.get('retrospective_value') else 'UNAVAILABLE'
        r['decision_feature_value']=eligible_decision_value(r,datetime.fromisoformat(r['cutoff_ist']))
        assert r['decision_feature_value'] is None
        r['revision_risk']='CURRENT_RETRIEVAL;HISTORICAL_VINTAGE_UNPROVEN'
        r['delivery_evidence_status']='NO_HISTORICAL_RECEIPT'
        r['completion_certification']='PROVIDER_BOUND_ONLY;GLOBAL_SESSION_DEFINITION_UNRESOLVED' if r.get('channel')=='PROVIDER_DAILY' else 'STRICT_BAR_END_CHECK;NOT_DELIVERY_PROOF'
        if r.get('retrospective_value') and r.get('bar_end_ist'):
            assert datetime.fromisoformat(r['bar_end_ist'])<datetime.fromisoformat(r['cutoff_ist'])
        out.append(r)
    csvout('HISTORICAL_AVAILABILITY_AUDIT.csv',out)

# Sector dashboard retains NSE/H10 channels and replaces obsolete global channels.
SECTOR_BRIDGE = r'''
<script src="global-market-data.js"></script><script>
(()=>{const old=TECH06.inspect;const recover=day=>{const rows=TECH06_GLOBAL.context[day]||[];const keys=new Set(rows.map(r=>r.series));const keep=(TECH06.data.context[day]||[]).filter(r=>!keys.has(r.series));TECH06.data.context[day]=keep.concat(rows.map(r=>({series:r.label+' / '+r.channel,phase:r.phase,category:r.category,verified_decision_time_value:r.verified_value,conservative_time_bounded_value:null,retrospective_context_value:r.retrospective_value,observation_date:r.observation_date,availability_upper_bound:null,missing_data_reason:r.reason+'; age '+r.age_minutes+' min; '+r.units})));};for(const day of Object.keys(TECH06_GLOBAL.context))recover(day);TECH06.inspect=day=>old(day);day.onchange=()=>TECH06.inspect(day.value);TECH06.inspect(TECH06.selectedDate);})();
</script>
'''

def presentation(bars):
    inv=inventory(bars)
    keys={r['series']:r['instrument_key'] for r in read(OUT/'GLOBAL_INSTRUMENT_DEFINITIONS.csv')}
    for item in inv:item['instrument_key']=keys[item['series']]
    save('CURRENT_DATA_INVENTORY.json',inv)
    data=jsdata(T/'charts/global-market-data.js','window.TECH06_GLOBAL=')
    data['status']=refresh_status(data['status'],bars)
    for rows in data['context'].values():
        for r in rows:
            r['verified_value']=eligible_decision_value(r,datetime.fromisoformat(r['cutoff_ist']))
            r['acceptance_state']='RETROSPECTIVE_ONLY' if r.get('retrospective_value') is not None else 'UNAVAILABLE'
            if r['series'] in ('DJIA','SP500'):
                r['cash_session_status']='QUARANTINED_INTERPRETATION'
                if 'CASH_SESSION_INTERPRETATION_QUARANTINED' not in r['reason']:
                    r['reason']+=';CASH_SESSION_INTERPRETATION_QUARANTINED'
    # No historical candle/indicator values are recalculated by presentation repairs.
    from experiments.technical_integration_v2.narrowed_charts import CONTEXT_JS, PAGE
    (T/'charts/global-market-data.js').write_text('window.TECH06_GLOBAL='+packed(data)+';\n')
    (T/'charts/global-context.js').write_text(CONTEXT_JS)
    (T/'charts/global-market.html').write_text(PAGE)
    sector=T/'charts/sector-rotation.html';s=sector.read_text()
    s=re.sub(r'\n<!-- TECH-FIX CURRENT CONTEXT -->.*?<!-- END TECH-FIX CURRENT CONTEXT -->','',s,flags=re.S)
    s=s.replace('</body>','\n<!-- TECH-FIX CURRENT CONTEXT -->'+SECTOR_BRIDGE+'<!-- END TECH-FIX CURRENT CONTEXT --></body>')
    sector.write_text(s)
    gift=next(r for r in inv if r['series']=='GIFT_NIFTY')
    text=f'''# Current technical acceptance — TECH-FIX-01

Complete technical block: NOT ACCEPTED. Bounded retrospective daily research only.

GIFT: {gift['accepted_daily']} accepted daily candles and {gift['accepted_minute']} sampled minute observations.
Identity: provider generic GLOBAL_INDEX|SGX NIFTY quotation. No verified futures contract, expiry, denomination or historical delivery.
Coverage and per-source hashes: ../technical-fix-v1/CURRENT_DATA_INVENTORY.json.

Dow/S&P cash-session interpretation is quarantined. Original provider timestamps and prices remain unchanged; 8/9 holiday labels do not identify cash sessions. No guessed date shift.
WTI 2026-06-18 and GIFT 2026-07-19 remain raw and quarantined, absent from accepted data and charts.
NSE: 5 of 139 catalog daily histories retained, 134 unacquired; only 2 comparable sectoral histories. Priority manifest contains 7 requests; none authorized or executed.
Zero historical decision-time certified values. Corporate actions and historical membership remain retrospective.
Validated indicator and index arithmetic is unchanged. G05–G08 limitations remain open.

Previous TECH-06 status is preserved in ../technical-fix-v1/evidence/TECH06_STATUS_BEFORE.md. Earlier reports and audit records remain historical evidence.
'''
    (T/'TECHNICAL_BLOCK_STATUS.md').write_text(text)
    (OUT/'CURRENT_STATUS.md').write_text(text)


def main():
    OUT.mkdir(parents=True,exist_ok=True);baseline();bars=validate_sources();sessions();definitions();nse();corporate();timing();presentation(bars)
    print('Offline repair complete; no acquisition, database, formula or raw-price changes.')
if __name__=='__main__':main()
