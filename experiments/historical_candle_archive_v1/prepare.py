"""Reuse retained canonical acquisition tables without mutating the company master."""
import csv
from datetime import date, timedelta
from decimal import Decimal
import gzip
import json
import sqlite3
from .storage import ROOT, OUT, dump, sha
from .identity import IdentityResolver

MASTER = ROOT/'artifacts/acquisition-readiness/AR-01.3C/attempt-02-post-canary/scraper.db'
METADATA = ROOT/'artifacts/earnings-reaction-v1/ta02b/public-nse-instruments.json.gz'
END = date(2026,11,4)  # Last entirely past date at initial HCD-01 preparation.
PILOT = ('KABRAEXTRU','RELIANCE','TCS','HDFCBANK','ICICIBANK','ITC','SUNPHARMA',
         'MARUTI','TATASTEEL','ULTRACEMCO','NTPC','BHARTIARTL','TITAN','ASIANPAINT',
         'INFY','LT','COALINDIA','PIDILITIND','AETHER','TIPSMUSIC')

def csvout(path, rows):
    if not rows:
        return
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,list(dict.fromkeys(k for r in rows for k in r)))
        w.writeheader();w.writerows(rows)

def windows(start, end, days):
    while start <= end:
        last=min(end,start+timedelta(days=days-1))
        yield str(start),str(last)
        start=last+timedelta(days=1)

def requests(company):
    key=company.get('instrument_key')
    if not key:
        return []
    specs=[('Weekly','weeks',1,'2000-01-01',str(END))]
    specs += [('Daily','days',1,a,b) for a,b in windows(date(2000,1,1),END,3650)]
    for tf,interval in [('1-hour',1),('4-hour',4)]:
        specs += [(tf,'hours',interval,a,b) for a,b in windows(date(2022,1,1),END,89)]
    result=[]
    for tf,unit,interval,a,b in specs:
        row=dict(company_id=company['company_id'],instrument_key=key,timeframe=tf,
                 unit=unit,interval=interval,from_date=a,to_date=b,role='NATIVE_HISTORY')
        row['request_id']=sha(json.dumps(row,sort_keys=True).encode())
        result.append(row)
    return result

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    latest=OUT/'latest-acquisition-tables.json'
    if latest.exists():
        # Ephemeral query projection of existing tables, never a replacement master.
        tables=json.loads(latest.read_text())
        c=sqlite3.connect(':memory:')
        for name,rows in tables.items():
            if not rows: continue
            columns=list(rows[0])
            schema=','.join('"'+k+'" '+('INTEGER' if any(isinstance(r[k],int) for r in rows) else 'TEXT') for k in columns)
            c.execute(f'CREATE TABLE "{name}" ({schema})')
            c.executemany(f'INSERT INTO "{name}" VALUES ({",".join("?" for _ in columns)})',
                          [tuple(r[k] for k in columns) for r in rows])
        c.commit()
    else:
        c=sqlite3.connect(f'file:{MASTER}?mode=ro',uri=True)
    c.row_factory=sqlite3.Row
    c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
    company={r['company_id']:dict(r) for r in c.execute('select * from companies')}
    scopes=[dict(r) for r in c.execute('select * from screener_collection_scope order by company_path')]
    history=[dict(r) for r in c.execute('select * from screener_scope_observations order by observation_id')]
    versions=[dict(r) for r in c.execute('''select r.company_id,s.exchange,s.segment,s.nse_symbol,s.bse_scrip_code,
        s.current_status,s.verification_status,v.* from exchange_securities s
        join exchange_security_versions v using(exchange_security_id)
        join exchange_company_links l using(exchange_security_id)
        join exchange_company_registry r using(registry_company_id)
        where r.company_id is not null order by r.company_id,s.exchange,v.version_id''')]
    identifiers=[dict(r) for r in c.execute('select * from security_identifiers order by company_id,security_identifier_id')]
    exchange_securities=[dict(r) for r in c.execute('select * from exchange_securities order by exchange_security_id')]
    c.rollback();c.close()
    metadata=json.loads(gzip.decompress(METADATA.read_bytes()))
    bse_path=OUT/'public-bse-instruments.json.gz'
    bse_metadata=json.loads(gzip.decompress(bse_path.read_bytes())) if bse_path.exists() else []
    identity_resolver=IdentityResolver(exchange_securities,identifiers,scopes,metadata+bse_metadata)
    identity_repairs=[]
    bse_isin={}
    for r in bse_metadata:
        if r.get('segment')=='BSE_EQ': bse_isin.setdefault(r.get('isin'),[]).append(r)
    by_isin={}
    for r in metadata:
        if r.get('segment')=='NSE_EQ' and r.get('instrument_type') in ('EQ','BE','BZ','SM','ST'):
            by_isin.setdefault(r.get('isin'),[]).append(r)
    eligible={}
    for s in scopes:
        if s['company_id'] and s['eligibility']=='eligible' and Decimal(s['market_cap_crore'] or '0')>580:
            eligible.setdefault(s['company_id'],[]).append(s)
    universe=[]
    for cid,observations in sorted(eligible.items()):
        cap=max(observations,key=lambda r:r['observed_at_utc'])
        ids=[r for r in identifiers if r['company_id']==cid]
        v=[r for r in versions if r['company_id']==cid]
        candidates={m['instrument_key']:m for s in v if s['exchange']=='NSE' and not s['valid_to_date']
                    for m in by_isin.get(s['isin'],[]) if s['nse_symbol']==m['trading_symbol']}
        record=dict(company_id=cid,name=company[cid]['current_legal_name'],
                    company_status=company[cid]['status'],market_cap_crore=cap['market_cap_crore'],
                    market_cap_observed_at=cap['observed_at_utc'],eligibility_source=cap['company_path'],
                    eligibility_evidence=cap['evidence_json'],cutoff_crore='580',
                    historical_membership='ONLY_DATED_RETAINED_SCOPE_OBSERVATIONS; never backdate current membership',
                    snapshot_status='LATEST_ACQUISITION_VOLUME_READ_ONLY_2026-11-05' if latest.exists() else 'RETAINED_2026-10-08',
                    instrument_key=None,symbol=None,listing_date=None,
                    mapping_status='UNRESOLVED_OR_UNSUPPORTED',corporate_action_status='UNKNOWN')
        if len(candidates)==1:
            m=next(iter(candidates.values()))
            record.update(instrument_key=m['instrument_key'],symbol=m['trading_symbol'],
                mapping_status='EXACT_RETAINED_NSE_ISIN_AND_SYMBOL; CURRENT_PROVIDER_NOT_REVERIFIED',
                listing_date=min((r['listing_date'] for r in v if r['exchange']=='NSE' and r['listing_date']),default=None))
        elif not candidates:
            bse={m['instrument_key']:m for s in v if s['exchange']=='BSE' and not s['valid_to_date']
                 for m in bse_isin.get(s['isin'],[]) if str(s['bse_scrip_code'])==str(m.get('exchange_token'))}
            if len(bse)==1:
                m=next(iter(bse.values()))
                record.update(instrument_key=m['instrument_key'],symbol=m['trading_symbol'],
                    mapping_status='EXACT_CURRENT_BSE_ISIN_AND_SCRIP_CODE; BSE_CALENDAR_REQUIRED',
                    listing_date=min((s['listing_date'] for s in v if s['exchange']=='BSE' and s['listing_date']),default=None))
        if not record['instrument_key']:
            resolved=identity_resolver.resolve(cid)
            identity_repairs.append(dict(company_id=cid,name=record['name'],**resolved))
            record['known_exchange_identifiers']=json.dumps(resolved.get('known_identifiers',
                [dict(exchange=r['identifier_type'],value=r['identifier_value']) for r in ids]),sort_keys=True)
            record['mapping_status']=resolved['status']
            if resolved['instrument_key']:
                record.update(instrument_key=resolved['instrument_key'],symbol=resolved['symbol'],
                              provider_isin=resolved['isin'],provider_instrument_type=resolved['instrument_type'],
                              historical_identity_continuity='UNVERIFIED; retain old ISINs and dated versions',
                              historical_isins=json.dumps(resolved['historical_isins']))
        universe.append(record)
    owners={}
    for r in universe:
        key=r['instrument_key']
        if key:
            if key in owners and owners[key]!=r['company_id']:
                raise ValueError('provider identity claimed by multiple canonical companies')
            owners[key]=r['company_id']
    pilot=[]
    previous_pilot=OUT/'pilot-companies.json'
    if previous_pilot.exists():
        pilot=json.loads(previous_pilot.read_text())
    else:
        for symbol in PILOT:
            found=[r for r in universe if r['symbol']==symbol]
            if len(found)!=1:
                raise ValueError(f'Pilot exact existing eligible identity required: {symbol}: {len(found)}')
            pilot.append(found[0])
    current_ids={r['company_id'] for r in universe}
    path_ids={s['company_path']:s['company_id'] for s in scopes}
    historical_observations={}
    for h in history:
        cid=path_ids.get(h['company_path'])
        if cid and cid not in current_ids and h['eligibility']=='eligible' and Decimal(h['market_cap_crore'] or '0')>580:
            historical_observations.setdefault(cid,[]).append(h)
    historical_extension=[]
    for cid,observations in sorted(historical_observations.items()):
        cap=max(observations,key=lambda r:r['observed_at_utc'])
        versions_c=[r for r in versions if r['company_id']==cid]
        candidates={m['instrument_key']:m for s in versions_c if s['exchange']=='NSE'
                    for m in by_isin.get(s['isin'],[]) if s['nse_symbol']==m['trading_symbol']}
        if not candidates:
            candidates={m['instrument_key']:m for s in versions_c if s['exchange']=='BSE'
                    for m in bse_isin.get(s['isin'],[]) if str(s['bse_scrip_code'])==str(m.get('exchange_token'))}
        provider=next(iter(candidates.values())) if len(candidates)==1 else None
        historical_extension.append(dict(company_id=cid,name=company[cid]['current_legal_name'],
            company_status=company[cid]['status'],currently_eligible=False,
            historical_eligibility_source=cap['company_path'],market_cap_crore=cap['market_cap_crore'],
            market_cap_observed_at=cap['observed_at_utc'],eligibility_evidence=cap['evidence_json'],
            historical_membership='Eligible at retained observation only; no backward extrapolation',
            instrument_key=provider['instrument_key'] if provider else None,
            symbol=provider['trading_symbol'] if provider else None,
            listing_date=min((r['listing_date'] for r in versions_c if r['listing_date']),default=None),
            mapping_status='EXACT_RETAINED_ISIN_AND_EXCHANGE_ID' if provider else 'UNRESOLVED',
            exchange_statuses=sorted({r['current_status'] for r in versions_c}),
            reason='RETAIN_HISTORICAL_ELIGIBILITY_DESPITE_ABSENCE_FROM_CURRENT_ABOVE580_SET'))
        extension=historical_extension[-1]
        if not extension['instrument_key']:
            resolved=identity_resolver.resolve(cid)
            extension['identity_evidence']=resolved
            if resolved['instrument_key']:
                extension.update(instrument_key=resolved['instrument_key'],symbol=resolved['symbol'],
                    mapping_status=resolved['status'],historical_isins=resolved['historical_isins'],
                    historical_identity_continuity='UNVERIFIED; dated security-version review required')
        key=extension['instrument_key']
        if key and key in owners and owners[key]!=cid:
            extension.update(instrument_key=None,mapping_status='CONFLICT_WITH_EXISTING_CANONICAL_OWNER',
                             conflicting_company_id=owners[key],candidate_provider_key=key)
        elif key:
            owners[key]=cid
    current_requests=[q for r in universe for q in requests(r)]
    historical_requests=[q for r in historical_extension for q in requests(r)]
    full=current_requests+historical_requests
    pilot_requests=[q for r in pilot for q in requests(r)]
    for r in pilot[:3]:
        q=dict(company_id=r['company_id'],instrument_key=r['instrument_key'],timeframe='1-minute',
               unit='minutes',interval=1,from_date='2026-10-26',to_date='2026-10-30',role='BOUNDARY_REFERENCE')
        q['request_id']=sha(json.dumps(q,sort_keys=True).encode());pilot_requests.insert(1,q)
    prior_manifest=OUT/'pilot-manifest.json'
    if prior_manifest.exists():
        for q in json.loads(prior_manifest.read_text())['requests']:
            if q['role']=='INCREMENTAL_OVERLAP_TEST' and q['request_id'] not in {r['request_id'] for r in pilot_requests}:
                pilot_requests.append(q)
    # First native Kabra request is the sole entitlement probe; no account endpoint.
    dump(OUT/'eligible-universe.json',universe)
    dump(OUT/'identity-linkage-repair.json',dict(
        company_master_modified=False,source='existing security identifiers, collection paths, exchange master and provider metadata',
        resolved=sum(bool(r['instrument_key']) for r in identity_repairs),
        remaining=sum(not r['instrument_key'] for r in identity_repairs),records=identity_repairs))
    csvout(OUT/'eligible-universe.csv',universe)
    dump(OUT/'pilot-companies.json',pilot)
    dump(OUT/'scope-observations.json',history)
    dump(OUT/'all-collection-scope.json',scopes)
    dump(OUT/'historical-security-versions.json',versions)
    dump(OUT/'existing-security-identifiers.json',identifiers)
    dump(OUT/'historically-eligible-extension.json',historical_extension)
    dump(OUT/'full-universe-manifest.json',dict(execution_enabled=False,
        approval_required=True,companies=universe,historically_eligible_extension=historical_extension,requests=full))
    dump(OUT/'pilot-manifest.json',dict(authorization='Owner HCD-01 request and continue; 20-company pilot only',
        companies=pilot,requests=pilot_requests))
    dump(OUT/'universe-summary.json',dict(eligible_canonical_companies=len(universe),
        scope_rows=len(scopes),eligible_scope_rows=sum(s['eligibility']=='eligible' for s in scopes),
        supported_exact_nse_companies=sum(bool(r['instrument_key'] and r['instrument_key'].startswith('NSE')) for r in universe),
        supported_exact_bse_companies=sum(bool(r['instrument_key'] and r['instrument_key'].startswith('BSE')) for r in universe),
        unresolved_companies=sum(not r['instrument_key'] for r in universe),
        historically_eligible_additional_companies=len(historical_extension),
        historically_eligible_mapped_companies=sum(bool(r['instrument_key']) for r in historical_extension),
        current_universe_native_requests=len(current_requests),historical_extension_native_requests=len(historical_requests),
        historical_scope_observations=len(history),security_versions=len(versions),
        historical_failures_policy='Retain all canonical scope rows and security versions including delisted/suspended/absent; historical nonmembers are not deleted. Acquisition candidates outside retained eligible set need dated eligibility review.',
        full_native_requests=len(full),pilot_requests=len(pilot_requests),
        source_master='trading-ai_scraper-data:/data/scraper.db read-only transaction' if latest.exists() else str(MASTER.relative_to(ROOT)),
        source_projection_sha256=sha(latest.read_bytes()) if latest.exists() else None,
        master_size=MASTER.stat().st_size,
        source_metadata=str(METADATA.relative_to(ROOT)),metadata_sha256=sha(METADATA.read_bytes()),
        current_live_source='Read-only latest acquisition-volume extraction; existing workers not started' if latest.exists() else 'Retained October snapshot',
        limitations=['Not all currently eligible companies are proven historically eligible since January 2022',
                    'Historical delisted universe and old capitalization series are incomplete',
                    'Unresolved/BSE-only mappings remain in manifest; no guessed Upstox key']))
    print(json.dumps(json.loads((OUT/'universe-summary.json').read_text()),indent=2))

if __name__=='__main__':
    main()
