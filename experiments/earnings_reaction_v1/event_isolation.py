"""ER-01 offline ledger, extending TA-01/TA-02 evidence gates. No acquisition.

prepare reads named retained snapshots, freezing selected rows and source bytes.
pilot and build replay that evidence without database access. Public filing timing
and earliest company-quarter release are independent assertions.
"""
from __future__ import annotations
import argparse
import collections
import csv
import hashlib
import json
import sqlite3
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

from .events import IST, aware
from .verification import Check, Outcome, publication_time, evidenced_calendar, verify_event_boundaries
from scrapper.sources.exchange.result_filings import parse_result_filings

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'artifacts/earnings-reaction-v1/event-isolation'
SS = ROOT / 'artifacts/subsector-ss04/research.sqlite3'
DB = ROOT / 'artifacts/acquisition-readiness/AR-01.3C/attempt-02-post-canary/scraper.db'
PERIODS = {'FY2026-Q4': ('2026-03-31', '2026-06-30'), 'FY2027-Q1': ('2026-06-30', '2026-09-30')}
# Frozen local audit cutoff, not a claim of complete exchange coverage.
CUTOFF = '2026-11-02T16:35:16+05:30'

def J(x):
    return json.dumps(x, sort_keys=True, ensure_ascii=False, separators=(',', ':'))

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + '\n')

def ro(p):
    wal = Path(str(p)+'-wal')
    if wal.exists() and wal.stat().st_size:
        raise ValueError('Retained snapshot has nonempty WAL: '+str(p))
    # An existing zero-byte WAL has no uncheckpointed frames; immutable read
    # preserves the read-only snapshot and its existing sidecars.
    c = sqlite3.connect(p.resolve().as_uri()+'?mode=ro&immutable=1', uri=True)
    c.row_factory = sqlite3.Row
    c.execute('pragma query_only=on')
    c.set_progress_handler(lambda: 1, 100_000_000)
    return c

def rows(c, sql, params=()):
    return [dict(r) for r in c.execute(sql, params)]

def retain(p):
    digest = sha(p)
    target = OUT/'evidence'/digest/p.name
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists(): target.write_bytes(p.read_bytes())
    return dict(original_path=str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p),
                retained_path=str(target.relative_to(OUT)), sha256=digest, bytes=p.stat().st_size)

def prepare():
    OUT.mkdir(parents=True, exist_ok=True)
    before = {str(p): [p.stat().st_size, p.stat().st_mtime_ns] for p in (SS, DB)}
    ss, db = ro(SS), ro(DB)
    cohort = rows(ss, '''select s.quarter,s.source_id,s.source_path,s.sha256 source_sha256,s.known_at source_known_at,
        r.*,coalesce(i.company_id,r.company_id) canonical_company_id,i.state overlay_state,i.evidence_json overlay_evidence,
        m.decision mentor_decision,m.known_at mentor_known_at,a.canonical_id canonical_tutor_id
        from ss_rows r join ss_sources s using(source_id)
        left join ss02_identity_overlay i using(row_id) left join ss_mentor_evidence m using(row_id)
        left join ss_subsectors t on t.label_raw=r.label_raw left join ss03_alias a using(subsector_id)
        where s.quarter in ('FY2026-Q4','FY2027-Q1') order by s.quarter,r.row_id''')
    ids = {r['canonical_company_id'] for r in cohort if r['canonical_company_id']}
    companies = rows(ss, 'select * from companies')
    identifiers = rows(ss, 'select * from security_identifiers')
    classifications = rows(ss, 'select * from ss04_decisions')
    source_manifests = []
    for r in rows(ss, "select * from ss_sources where quarter in ('FY2026-Q4','FY2027-Q1')"):
        original = Path(r['source_path'])
        target = OUT/'evidence'/r['sha256']/original.name
        target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(r['content'])
        assert sha(target) == r['sha256']
        source_manifests.append(dict(source_id=r['source_id'],quarter=r['quarter'],original_path=str(original),
            retained_path=str(target.relative_to(OUT)),sha256=r['sha256'],original_present=original.is_file(),
            original_hash_matches=sha(original)==r['sha256'] if original.is_file() else None))
    # Existing registry links resolve BSE numeric codes; no name/fuzzy joins.
    securities = rows(db, 'select * from exchange_securities')
    links = rows(db, '''select l.*,r.company_id,r.isin from exchange_company_links l
        join exchange_company_registry r using(registry_company_id) where r.company_id is not null''')
    versions = rows(db, 'select * from exchange_security_versions')
    snapshot_ids = set()
    filings = rows(db, "select * from exchange_result_filings where result_period in ('2026-03','2026-06','2026-09') order by filing_id")
    keymap = collections.defaultdict(set)
    for r in identifiers:
        if r['identifier_type'] in ('NSE','BSE'):
            keymap[(r['identifier_type'],r['identifier_value'])].add(r['company_id'])
    secmap = {r['exchange_security_id']:r for r in securities}
    for r in links:
        s = secmap[r['exchange_security_id']]
        keymap[(s['exchange'],s['exchange_key'])].add(r['company_id'])
    selected = []
    for r in filings:
        match = keymap[(r['exchange'],r['exchange_key'])]
        if not match.intersection(ids): continue
        r['company_candidates'] = sorted(match)
        r['canonical_company_id'] = next(iter(match)) if len(match)==1 else None
        selected.append(r); snapshot_ids.add(r['snapshot_id'])
    sources = rows(db, '''select s.*,b.local_path,b.content_sha256,b.byte_size from exchange_source_snapshots s
        join response_blobs b using(blob_id) order by snapshot_id''')
    sources = [r for r in sources if r['snapshot_id'] in snapshot_ids]
    # Search actual retained file names, including alternate local mounts, by hash.
    local = collections.defaultdict(list)
    for base in [ROOT/'scrapper/store/snapshots/exchange', ROOT/'artifacts']:
        for p in base.rglob('*'):
            if p.is_file() and p.suffix in ('.csv','.raw','.html','.xml') and len(p.stem)==64:
                local[p.stem].append(p)
    for s in sources:
        candidates = [Path(s['local_path']), *local[s['content_sha256']]]
        s['retained_source'] = next((retain(p) for p in candidates if p.is_file() and sha(p)==s['content_sha256']), None)
    # Replay original feed bytes with the canonical production parser (read only).
    feeds = []
    for p in sorted((ROOT/'scrapper/store/snapshots/exchange/NSE/nse_financial_results').rglob('*.csv')):
        feeds.append((p,'nse_financial_results'))
    for p in sorted((ROOT/'artifacts/bse-browser-results').glob('*.raw')):
        feeds.append((p,'bse_financial_results'))
    parsed = []
    feed_inventory = []
    for p,dataset in feeds:
        result = parse_result_filings(p.read_bytes(),dataset)
        evidence = retain(p)
        feed_inventory.append(dict(**evidence,dataset=dataset,accepted=len(result.filings),rejected=len(result.rejected),
            periods=dict(collections.Counter(f.result_period for f in result.filings))))
        for f in result.filings:
            match = keymap[(f.exchange,f.exchange_key)]
            if f.period_end_date not in {'2026-03-31','2026-06-30','2026-09-30'} or not match.intersection(ids): continue
            parsed.append(dict(exchange=f.exchange,exchange_key=f.exchange_key,isin=f.isin,filing_key=f.filing_key,
                period_end_date=f.period_end_date,reporting_scope=f.reporting_scope,raw_row=dict(f.raw_row),
                broadcast_at_utc=f.broadcast_at_utc,company_candidates=sorted(match),
                canonical_company_id=next(iter(match)) if len(match)==1 else None,
                source=evidence,row_position=f.row_position))
    # Freeze real provider responses, verifying their manifest hashes. Indices remain outside company coverage.
    candle_datasets = []
    base = ROOT/'artifacts/earnings-reaction-v1'
    ex = json.loads((base/'ta02b/ta02d-execution.json').read_text())
    for d in ex['datasets']:
        request = ex['requests'][d['number']-1]
        p = base/'ta02b/ta02d-retrieved'/f"{d['number']}-{request['response_sha256']}.json"
        assert sha(p)==request['response_sha256']
        candle_datasets.append(dict(instrument_key=d['instrument_key'],unit=d['unit'],interval=d['interval'],
            requested_from=d['requested_from'],requested_to=d['requested_to'],source=retain(p)))
    manifest = json.loads((base/'ta03r4/data-source-manifest.json').read_text())
    for d in manifest['datasets']:
        p = ROOT/d['raw_path']; assert sha(p)==d['raw_sha256']
        req=d['source_request']
        candle_datasets.append(dict(instrument_key=req['instrument_key'],unit=req['unit'],interval=req['interval'],
            requested_from=req['from_date'],requested_to=req['to_date'],source=retain(p)))
    ancillary = []
    for rel in ['ta02a/calendar-evidence.json','ta02a/source-evidence.json','ta01/event-sample-manifest.json',
                'ta02b/public-nse-instruments.json.gz','ta03r4/instrument-identity.json',
                'ta03r4/session-schedule-proposal.json','ta03r3/session-schedule.json']:
        ancillary.append(retain(base/rel))
    acq_names={r['company_id']:r['current_legal_name'] for r in rows(db,'select company_id,current_legal_name from companies')}
    identity_audit=[dict(company_id=r['company_id'],ss04_name=r['current_legal_name'],acquisition_snapshot_name=acq_names.get(r['company_id']),agrees=r['current_legal_name']==acq_names.get(r['company_id'])) for r in companies if r['company_id'] in ids]
    assert all(r['agrees'] for r in identity_audit)
    # Original stored document types cannot establish actual results release.
    doc_schema = db.execute("select sql from sqlite_master where name='document_artifacts'").fetchone()[0]
    ss.close();db.close()
    assert before == {str(p): [p.stat().st_size,p.stat().st_mtime_ns] for p in (SS,DB)}
    payload = dict(version='ER-01-v1', cutoff=CUTOFF, cutoff_basis='ER-01 read-only local evidence audit at 2026-11-02 11:05:16 UTC; latest BSE surface is dated October 31; collection is partial, not negative publication evidence',
        databases=before,identity_audit=identity_audit,feed_provenance='Standalone NSE file bytes retained and parsed; acquisition receipt not reconciled. BSE browser sidecars retain requested URLs but creation fields are not public dissemination.',cohort=cohort,companies=[r for r in companies if r['company_id'] in ids],
        identifiers=[r for r in identifiers if r['company_id'] in ids],classifications=[r for r in classifications if r['company_id'] in ids],
        sources=sources,source_manifests=source_manifests,filings=selected,parsed_filings=parsed,
        securities=securities,links=links,versions=versions,feed_inventory=feed_inventory,candle_datasets=candle_datasets,
        ancillary=ancillary,document_artifacts_schema=doc_schema,
        original_disclosure_review={}, preservation_size_mtime_unchanged=True)
    dump(OUT/'inputs.json',payload)
    print(J(dict(cohort_rows=len(cohort),stored_filings=len(selected),source_byte_filings=len(parsed),sources=len(sources))))


def phase(stamp):
    """Clock-only diagnostic. Does NOT assert session existence or holiday status."""
    if stamp is None:return 'UNKNOWN'
    t=stamp.astimezone(IST).strftime('%H:%M:%S')
    return 'PRE_OPEN_CLOCK_ONLY' if t<'09:15:00' else 'AFTER_CLOSE_CLOCK_ONLY' if t>='15:30:00' else 'INTRADAY_CLOCK_ONLY'


def candle_window(start, end, release, next_release=None, cutoff=None):
    """Half-open [release,next_release): bar close equal to next release is safe.

    The complete OHLCV interval must fit. A cutoff is required when censored.
    Caller must independently certify session, identity, finality and gaps.
    """
    for t in (start,end,release):aware(t)
    limits=[t for t in (next_release,cutoff) if t is not None]
    if not limits:return False
    for t in limits:aware(t)
    limit=min(limits)
    return start < end and release <= start and end <= limit and start < limit


def research_status(company_resolved, start_verified, next_verified, data_complete):
    if not company_resolved:return 'IDENTITY_UNRESOLVED'
    if not start_verified:return 'START_UNVERIFIED'
    if not next_verified:return 'VERIFIED_RIGHT_CENSORED'
    return 'VERIFIED_COMPLETE' if data_complete else 'DATA_INCOMPLETE'


def first_candles(release, next_release, calendar, minutes, observed_starts=None, cutoff=None):
    """Explicit session-open anchored bars. No joining across session closures.

    Truncated session-tail bars are explicitly labelled; never advertised as full
    nominal duration. observed_starts lets a caller suppress missing/halted bars.
    """
    aware(release)
    if minutes is not None and (type(minutes) is not int or minutes<=0):raise ValueError('positive interval required')
    if calendar is None:return []
    if not calendar.covered_from <= release.astimezone(IST).date() <= calendar.covered_through:return []
    limit=next_release or cutoff
    if limit is None:return []
    aware(limit)
    # Existing TA-01 calendar validates timezone and session assignment.
    result=[]
    for session in calendar.sessions:
        start=session.opens_at
        while start<session.closes_at:
            end=session.closes_at if minutes is None else min(start+timedelta(minutes=minutes),session.closes_at)
            if candle_window(start,end,release,next_release,cutoff):
                if observed_starts is None or start in observed_starts:
                    result.append(dict(start=start.isoformat(),end=end.isoformat(),completed_at=end.isoformat(),
                        truncated=minutes is not None and end-start<timedelta(minutes=minutes)))
            start=end
    return result


def evidence_records(p):
    source={r['snapshot_id']:r for r in p['sources']}
    output=[]
    for r in p['filings']:
        s=source[r['snapshot_id']]
        output.append(dict(record_id='stored:'+str(r['filing_id']),company_id=r['canonical_company_id'],
            company_candidates=r['company_candidates'],exchange=r['exchange'],exchange_key=r['exchange_key'],
            isin=r['isin'],period_end=r['period_end_date'],scope=r['reporting_scope'],filing_key=r['filing_key'],
            raw=json.loads(r['raw_row_json']),stored_broadcast_at_utc=r['broadcast_at_utc'],
            evidence_locator=str(DB.relative_to(ROOT))+':exchange_result_filings:'+str(r['filing_id']),
            source_blob_locator=s['local_path'],source_hash=s['content_sha256'],
            source=s['retained_source'],row_position=None,source_capture=s['observed_at_utc']))
    for r in p['parsed_filings']:
        output.append(dict(record_id='feed:'+r['source']['sha256']+':'+str(r['row_position']),
            company_id=r['canonical_company_id'],company_candidates=r['company_candidates'],exchange=r['exchange'],
            exchange_key=r['exchange_key'],isin=r['isin'],period_end=r['period_end_date'],scope=r['reporting_scope'],
            filing_key=r['filing_key'],raw=r['raw_row'],stored_broadcast_at_utc=r['broadcast_at_utc'],
            evidence_locator=r['source']['original_path']+':row:'+str(r['row_position']),
            source_blob_locator=r['source']['original_path'],source_hash=r['source']['sha256'],source=r['source'],
            row_position=r['row_position'],source_capture=None))
    for r in output:
        raw=r['raw']; ref=r['evidence_locator']
        # TA-02A publication gate: explicit public field plus retained source bytes.
        field=raw.get('EXCHANGE DISSEMINATION TIME') or raw.get('exchdisstime')
        stamp,check=publication_time({'EXCHANGE DISSEMINATION TIME':field or ''},field_evidence=Check(
            Outcome.VERIFIED if r['source'] else Outcome.UNRESOLVED,
            'hash_verified_retained_exchange_feed' if r['source'] else 'source_bytes_missing',ref))
        candidate_stamp=stamp
        # Raw bytes corroborate transcription, but these standalone local feeds
        # have no reconciled acquisition receipt in either retained database.
        # A checksum alone cannot independently authenticate exchange origin.
        if r['record_id'].startswith('feed:'):
            stamp=None
            check=Check(Outcome.UNRESOLVED,'feed_acquisition_provenance_unreconciled',ref)
        r.update(filing_dissemination_ist=stamp.isoformat() if stamp else None,
            retained_field_dissemination_ist=candidate_stamp.isoformat() if candidate_stamp else None,
            dissemination_check=check.reason,raw_dissemination=field,
            raw_broadcast=raw.get('BROADCAST DATE/TIME') or raw.get('broadCastDate') or raw.get('Fld_CreateDate'),
            raw_revision=raw.get('REVISED DATE/TIME'),filing_type=raw.get('TYPE OF SUBMISSION','UNSPECIFIED'),
            source_url=raw.get('DETAILS') or raw.get('Resultpageurl') or raw.get('xbrl') or raw.get('XBRL') or raw.get('URL'),
            relationship_status='UNREVIEWED_EARLIER_ANNOUNCEMENTS_AND_PARENT_LINKS',
            original_results_timestamp_ist=None,publication_confidence='FILING_FIELD_VERIFIED_EARLIEST_RELEASE_UNRESOLVED' if stamp else 'PUBLIC_DISSEMINATION_UNVERIFIED',
            session_status='CALENDAR_UNVERIFIED',clock_phase=phase(stamp),
            identity_status='CANONICAL_LINK_RESOLVED_HISTORICAL_VALIDITY_UNVERIFIED' if r['company_id'] else 'IDENTITY_UNRESOLVED')
        r['native_announcement_id']=raw.get('seqNumber') or None
        r['clock_phase']=phase(candidate_stamp or datetime.fromisoformat(r['stored_broadcast_at_utc']))
        r['clock_phase_basis']='RETAINED_FIELD_DIAGNOSTIC_ONLY' if candidate_stamp else 'UNVERIFIED_STORED_BROADCAST_DIAGNOSTIC_ONLY'
        r['same_filing_identity']=r['exchange']+'|'+r['filing_key']
    return sorted(output,key=lambda r:r['record_id'])


def load():
    p=json.loads((OUT/'inputs.json').read_text())
    for r in p['feed_inventory']+p['ancillary']+p['source_manifests']+[d['source'] for d in p['candle_datasets']]:
        assert sha(OUT/r['retained_path'])==r['sha256']
    return p


def pilot():
    p=load(); evidence=evidence_records(p)
    chosen=[]
    # Six per quarter, maximizing actual filing declaration and clock conditions.
    for q,(period,_) in PERIODS.items():
        members={r['canonical_company_id'] for r in p['cohort'] if r['quarter']==q}
        eligible=[r for r in evidence if r['company_id'] in members and r['period_end']==period]
        groups=collections.defaultdict(list)
        for r in eligible:groups[(r['clock_phase'],r['filing_type'],bool(r['source']))].append(r)
        seen=set()
        for group in sorted(groups):
            for r in groups[group]:
                if r['company_id'] not in seen:
                    chosen.append(dict(quarter=q,company_id=r['company_id'],record_id=r['record_id'],
                        clock_phase=r['clock_phase'],filing_type=r['filing_type'],
                        source_bytes_verified=bool(r['source']),filing_dissemination_ist=r['retained_field_dissemination_ist'],
                        original_boundary='START_UNVERIFIED',reason='First public actual-results disclosure and earlier announcements not reconciled',
                        evidence_locator=r['evidence_locator']))
                    seen.add(r['company_id']);break
            if len(seen)==6:break
        for r in eligible:
            if len(seen)==6:break
            if r['company_id'] not in seen:
                chosen.append(dict(quarter=q,company_id=r['company_id'],record_id=r['record_id'],clock_phase=r['clock_phase'],
                    filing_type=r['filing_type'],source_bytes_verified=bool(r['source']),
                    filing_dissemination_ist=r['retained_field_dissemination_ist'],original_boundary='START_UNVERIFIED',
                    reason='First public actual-results disclosure and earlier announcements not reconciled',evidence_locator=r['evidence_locator']))
                seen.add(r['company_id'])
    dump(OUT/'PILOT.json',dict(events=chosen,pass_gate='12 real events examined; zero unsupported original boundaries promoted',
        unavailable_conditions='No certified pre-market/holiday/special live session in retained sample; synthetic boundary tests required',
        demonstrated_boundary_defects='No existing gate defect demonstrated; new end-boundary extension tested separately'))
    print(J(dict(pilot_events=len(chosen),quarters=dict(collections.Counter(r['quarter'] for r in chosen)))))


def write_csv(name, data, fields=None, *, out=None):
    data=list(data);fields=fields or list(data[0])
    with ((out or OUT)/name).open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for r in data:w.writerow({k:J(v) if isinstance(v,(dict,list)) else v for k,v in r.items()})


def build(*, payload=None, output_dir=None, verification_reviews=None, evidence_root=None):
    p=load() if payload is None else payload
    dest=Path(output_dir) if output_dir is not None else OUT
    dest.mkdir(parents=True,exist_ok=True)
    reviews=verification_reviews if verification_reviews is not None else p.get('original_disclosure_review',{})
    review_root=Path(evidence_root) if evidence_root is not None else dest
    emit=lambda name,data,fields=None: write_csv(name,data,fields,out=dest)
    if payload is None:assert len(json.loads((OUT/'PILOT.json').read_text())['events'])==12
    ev=evidence_records(p)
    by=collections.defaultdict(list)
    for r in ev:
        if r['company_id']:by[(r['company_id'],r['period_end'])].append(r)
    co=collections.defaultdict(list)
    for r in p['cohort']:co[(r['quarter'],r['canonical_company_id'] or r['row_id'])].append(r)
    classmap={r['company_id']:json.loads(r['decision_json']) for r in p['classifications']}
    namemap={r['company_id']:r['current_legal_name'] for r in p['companies']}
    idmap=collections.defaultdict(list)
    for r in p['identifiers']:idmap[r['company_id']].append(r)
    secmap={r['exchange_security_id']:r for r in p['securities']}
    isins=collections.defaultdict(set)
    for r in p['links']:
        if r['company_id']:isins[r['company_id']].add(r['isin'])
    prices=collections.defaultdict(lambda:collections.defaultdict(dict))
    price_inventory=[]
    for d in p['candle_datasets']:
        raw=json.loads((OUT/d['source']['retained_path']).read_text())
        bars=raw['data']['candles']; key=d['instrument_key'];unit=d['unit']
        for bar in bars:
            stamp=bar[0];old=prices[key][unit].get(stamp)
            if old is not None and old!=bar:raise ValueError('conflicting retained provider candle')
            prices[key][unit][stamp]=bar
        price_inventory.append(dict(**d,rows=len(bars),first=min(b[0] for b in bars),last=max(b[0] for b in bars)))
    ledger=[]; memberships=[]; coverage=[]; unresolved=[]
    for (quarter,cid), members in sorted(co.items(),key=lambda x:(x[0][0],str(x[0][1]))):
        period,nextperiod=PERIODS[quarter]; canonical=cid if isinstance(cid,int) else None
        event=f'{quarter}:{cid}'; start=by[(canonical,period)];nxt=by[(canonical,nextperiod)]
        classification=classmap.get(canonical,{})
        symbols=sorted({r['identifier_value'] for r in idmap[canonical] if r['identifier_type']=='NSE'})
        bse=sorted({s['exchange_key'] for link in p['links'] if link['company_id']==canonical
            for s in [secmap[link['exchange_security_id']]] if s['exchange']=='BSE'})
        verification=verify_event_boundaries(reviews.get(event,{}),company_id=canonical,period_end=period,
            next_period_end=nextperiod,cutoff=p['cutoff'],evidence_root=review_root)
        # Source timing is independent of missing prices and historical market mappings.
        boundary_status=('BOUNDARY_CONFLICT' if verification['reasons'] else 'VERIFIED_COMPLETE' if verification['complete'] else 'VERIFIED_RIGHT_CENSORED'
            if verification['start_verified'] else 'START_UNVERIFIED')
        status=('BOUNDARY_CONFLICT' if verification['reasons'] else research_status(bool(canonical),verification['start_verified'],verification['complete'],
            p.get('market_readiness',{}).get(event,{}).get('complete',False)))
        limitations=([] if verification['start_verified'] else ['EARLIEST_PUBLIC_RESULTS_DISCLOSURE_UNRECONCILED'])+['CALENDAR_HOURS_SPECIAL_SESSIONS_UNVERIFIED',
            'HISTORICAL_INSTRUMENT_CONTINUITY_UNVERIFIED']
        if not start and not verification['start']['candidate']:limitations.append('NO_RETAINED_START_RESULTS_FILING')
        elif start and not verification['start']['timestamp_verified'] and not any(r['source'] for r in start):limitations.append('START_FEED_BYTES_MISSING')
        elif start and not verification['start']['timestamp_verified'] and not any(r['filing_dissemination_ist'] for r in start):limitations.append('PUBLIC_DISSEMINATION_PROVENANCE_UNVERIFIED')
        if not nxt and not verification['next']['candidate']:limitations.append('NO_RETAINED_NEXT_QUARTER_FILING_NOT_PROOF_UNPUBLISHED')
        keys=sorted(k for k in prices if k.startswith('NSE_EQ|') and k.split('|')[1] in isins[canonical])
        daily={};intraday={}
        for key in keys:
            daily.update(prices[key]['days']);intraday.update(prices[key]['minutes'])
        if not daily:limitations.append('NO_RETAINED_DAILY_CANDLES')
        if not intraday:limitations.append('NO_RETAINED_INTRADAY_SOURCE_CANDLES')
        if daily or intraday:limitations.append('EVENT_RELATIVE_COVERAGE_BLOCKED_BY_START_UNVERIFIED')
        row=dict(event_id=event,company_id=canonical,company_name=namemap.get(canonical,members[0]['company_name_raw']),
            quarter=quarter,period_end=period,next_period_end=nextperiod,nse_symbols=symbols,bse_scrip_codes=bse,
            identity_status='SS04_CANONICAL_RESOLVED' if canonical else 'IDENTITY_UNRESOLVED',
            historical_instrument_status='UNVERIFIED_EFFECTIVE_DATES_TRADABILITY_CONTINUITY',
            eligibility=status,boundary_status=boundary_status,market_data_status='DATA_INCOMPLETE',
            candidate_filing_stage='PRESENT' if start or verification['start']['candidate'] else 'MISSING',
            start_timestamp_stage='VERIFIED' if verification['start']['timestamp_verified'] else 'UNRESOLVED',
            earliest_start_stage='VERIFIED' if verification['start_verified'] else 'UNRESOLVED',
            next_timestamp_stage='VERIFIED' if verification['next']['timestamp_verified'] else 'UNRESOLVED',
            next_disclosure_stage='VERIFIED' if verification['next_verified'] else 'UNRESOLVED',
            event_window_stage='VERIFIED' if verification['complete'] else 'UNRESOLVED',
            verification_details=verification,
            announcement_at_ist=verification['start']['publication_at_ist'] if verification['start']['timestamp_verified'] else None,
            next_announcement_at_ist=verification['next']['publication_at_ist'] if verification['next']['timestamp_verified'] else None,
            window_start_inclusive_ist=verification['start_inclusive_ist'],window_end_exclusive_ist=verification['end_exclusive_ist'],
            observation_cutoff_ist=p['cutoff'],censoring_status=('NOT_CENSORED' if verification['complete'] else 'NEXT_BOUNDARY_UNESTABLISHED' if verification['start_verified'] else 'START_UNVERIFIED_NEXT_BOUNDARY_UNESTABLISHED'),
            first_tradable_observation_ist=None,first_daily_start_ist=None,first_daily_completed_ist=None,
            first_4h_start_ist=None,first_4h_completed_ist=None,first_75m_start_ist=None,first_75m_completed_ist=None,
            first_1h_start_ist=None,first_1h_completed_ist=None,
            start_filing_count=len({r['same_filing_identity'] for r in start})+int(not start and verification['start']['candidate']),next_filing_count=len({r['same_filing_identity'] for r in nxt})+int(not nxt and verification['next']['candidate']),
            start_evidence_ids=[r['record_id'] for r in start],next_evidence_ids=[r['record_id'] for r in nxt],
            candidate_start_clock_phases=sorted({r['clock_phase'] for r in start}),
            candidate_next_clock_phases=sorted({r['clock_phase'] for r in nxt}),
            retained_start_filing_disseminations=sorted({r['retained_field_dissemination_ist'] for r in start if r['retained_field_dissemination_ist']}),
            retained_next_filing_disseminations=sorted({r['retained_field_dissemination_ist'] for r in nxt if r['retained_field_dissemination_ist']}),
            verified_start_filing_disseminations=sorted({r['filing_dissemination_ist'] for r in start if r['filing_dissemination_ist']} | ({verification['start']['publication_at_ist']} if verification['start']['timestamp_verified'] else set())),
            verified_next_filing_disseminations=sorted({r['filing_dissemination_ist'] for r in nxt if r['filing_dissemination_ist']} | ({verification['next']['publication_at_ist']} if verification['next']['timestamp_verified'] else set())),
            canonical_subsector=classification.get('primary_code'),classification_status=classification.get('status'),
            classification_known_at=classification.get('known_at'),classification_event_date_availability='UNKNOWN_RETROSPECTIVE_DO_NOT_BACKDATE',
            limitations=limitations)
        ledger.append(row)
        for m in members:
            memberships.append(dict(event_id=event,company_id=canonical,quarter=quarter,source_id=m['source_id'],source_path=m['source_path'],
                source_sha256=m['source_sha256'],row_id=m['row_id'],source_row_locator=m['row_ref'],symbol_raw=m['symbol_raw'],
                exchange_raw=m['exchange_raw'],original_tutor_label=m['label_raw'],canonical_tutor_id=m['canonical_tutor_id'],
                original_membership='PRESENT_IN_SUPPLIED_ROSTER',mentor_decision=m['mentor_decision'],
                explicit_selection_decision='NOT_ESTABLISHED_BY_THIS_SOURCE' if m['mentor_decision']=='unknown' else 'FOCUS_REFERENCE',
                mentor_known_at=m['mentor_known_at'],source_known_at=m['source_known_at'],
                resolution_state=m['resolution_state'],resolution_json=m['resolution_json'],overlay_evidence=m['overlay_evidence'],
                independent_classification=classification,absence_semantics='UNKNOWN_NEVER_EXPLICIT_REJECTION'))
        coverage.append(dict(event_id=event,company_id=canonical,instrument_keys=keys,identity_mapping_basis='SS04 company + retained ISIN registry; historical validity UNVERIFIED',
            daily_rows=len(daily),daily_first=min(daily) if daily else None,daily_last=max(daily) if daily else None,
            intraday_source_rows=len(intraday),intraday_first=min(intraday) if intraday else None,intraday_last=max(intraday) if intraday else None,
            intraday_source_interval_minutes=5 if intraday else None,pre_results_daily='UNRESOLVED_EVENT_TIME' if daily else 'MISSING',
            post_results_daily='UNRESOLVED_EVENT_TIME' if daily else 'MISSING',next_boundary_coverage='UNRESOLVED_NEXT_EVENT_TIME',
            four_hour='SOURCE_5M_PARTIAL_HISTORY_CALENDAR_UNVERIFIED' if intraday else 'MISSING_SOURCE_CANDLES',
            seventy_five_minute='SOURCE_5M_PARTIAL_HISTORY_CALENDAR_UNVERIFIED' if intraday else 'MISSING_SOURCE_CANDLES',
            one_hour='SOURCE_5M_PARTIAL_HISTORY_CALENDAR_UNVERIFIED' if intraday else 'MISSING_SOURCE_CANDLES',
            missing_intervals='Exact event-relative missing intervals indeterminate until original publication and calendar verified',
            suspension_status='UNKNOWN_NO_BAR_IS_NOT_SUSPENSION_EVIDENCE',adjustment_basis='UNKNOWN_PROVIDER_ADJUSTMENT_BASIS',
            evidence_sources=[d['source']['original_path'] for d in price_inventory if d['instrument_key'] in keys]))
        for cause in limitations:
            unresolved.append(dict(event_id=event,company_id=canonical,quarter=quarter,cause=cause,
                needed_evidence={'EARLIEST_PUBLIC_RESULTS_DISCLOSURE_UNRECONCILED':'Original actual-results attachment + complete earlier BSE/NSE announcement review; explicit public dissemination and correction parent links',
                'CALENDAR_HOURS_SPECIAL_SESSIONS_UNVERIFIED':'Dated trading-hours and complete closure/special-live/mock session ledger',
                'HISTORICAL_INSTRUMENT_CONTINUITY_UNVERIFIED':'Effective-dated issuer/ISIN/symbol/series/status changes covering event through next event'}.get(cause,cause),
                retained_start_evidence_ids=row['start_evidence_ids'],retained_next_evidence_ids=row['next_evidence_ids']))
    emit('EVENT_LEDGER.csv',ledger);emit('COHORT_MEMBERSHIP.csv',memberships);emit('DATA_COVERAGE.csv',coverage)
    emit('UNRESOLVED_EVENTS.csv',unresolved)
    flat=[]
    for r in ev:
        flat.append({**{k:v for k,v in r.items() if k not in ('source','raw')},
            'retained_evidence_path':r['source']['retained_path'] if r['source'] else None,'raw_row_json':r['raw']})
    emit('EXCHANGE_EVIDENCE.csv',flat,list(flat[0]) if flat else ['record_id','company_id','evidence_locator','raw_row_json'])
    windows=[dict(event_id=r['event_id'],company_id=r['company_id'],quarter=r['quarter'],
        start_inclusive_ist=r['window_start_inclusive_ist'],end_exclusive_ist=r['window_end_exclusive_ist'],
        observation_cutoff_ist=r['observation_cutoff_ist'],boundary_status=r['boundary_status'],
        first_tradable_observation_ist=r['first_tradable_observation_ist']) for r in ledger if r['event_window_stage']=='VERIFIED']
    emit('EVENT_WINDOWS.csv',windows,['event_id','company_id','quarter','start_inclusive_ist','end_exclusive_ist','observation_cutoff_ist','boundary_status','first_tradable_observation_ist'])
    dump(dest/'PRICE_SOURCE_INVENTORY.json',price_inventory)
    summary=dict(events=len(ledger),unique_companies=len({r['company_id'] for r in ledger}),
        quarters=dict(collections.Counter(r['quarter'] for r in ledger)),verified_original_boundaries=sum(r['earliest_start_stage']=='VERIFIED' for r in ledger),verified_next_boundaries=sum(r['next_disclosure_stage']=='VERIFIED' for r in ledger),
        complete_windows=len(windows),right_censored_verified_starts=sum(r['boundary_status']=='VERIFIED_RIGHT_CENSORED' for r in ledger),unresolved_windows=len(ledger)-len(windows),
        start_events_with_candidates=sum(r['start_filing_count']>0 for r in ledger),next_events_with_candidates=sum(r['next_filing_count']>0 for r in ledger),
        events_with_retained_start_filing_field=sum(bool(r['retained_start_filing_disseminations']) for r in ledger),
        events_with_retained_next_filing_field=sum(bool(r['retained_next_filing_disseminations']) for r in ledger),
        events_with_verified_start_filing_field=sum(bool(r['verified_start_filing_disseminations']) for r in ledger),
        events_with_verified_next_filing_field=sum(bool(r['verified_next_filing_disseminations']) for r in ledger),
        daily_events=sum(r['daily_rows']>0 for r in coverage),intraday_events=sum(r['intraday_source_rows']>0 for r in coverage),
        daily_unique_companies=len({r['company_id'] for r in coverage if r['daily_rows']}),
        causes=dict(collections.Counter(r['cause'] for r in unresolved)),evidence_records=len(ev),
        independently_verified_windows=len(windows),cutoff=p['cutoff'])
    dump(dest/'SUMMARY.json',summary)
    print(J(summary))


def package():
    paths=[p for p in OUT.rglob('*') if p.is_file() and p.suffix!='.zip' and p.name!='SHA256SUMS']
    paths += [p for p in (ROOT/'experiments/earnings_reaction_v1').glob('*.py') if p.name in
        ('__init__.py','event_isolation.py','events.py','verification.py','candles.py','upstox_candles.py','investigate_sample.py')]
    paths += [ROOT/'artifacts/earnings-reaction-v1/ta02b/README.md']
    paths += [ROOT/'experiments/earnings_reaction_v1/tests'/n for n in ('test_event_isolation.py','test_events.py','test_verification.py','test_candles.py')]
    for folder in ('ta01','ta02a'):
        paths += [q for q in (ROOT/'artifacts/earnings-reaction-v1'/folder).iterdir() if q.is_file() and q.suffix in ('.json','.csv','.md')]
    # Canonical parsers and imports required to replay in the existing repository.
    paths += [ROOT/r for r in ['scrapper/sources/exchange/result_filings.py','scrapper/sources/exchange/tabular.py',
        'scrapper/sources/exchange/security_master.py','scrapper/core/nums.py','TRADING_AI_NORTH_STAR.md']]
    paths=sorted(set(paths))
    (OUT/'SHA256SUMS').write_text(''.join(f'{sha(p)}  {p.relative_to(ROOT)}\n' for p in paths))
    paths.append(OUT/'SHA256SUMS')
    with zipfile.ZipFile(OUT/'ER-01-review.zip','w',compression=zipfile.ZIP_DEFLATED) as z:
        for p in paths:
            info=zipfile.ZipInfo(str(p.relative_to(ROOT)),date_time=(2026,11,2,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            z.writestr(info,p.read_bytes())
    print(str(OUT/'ER-01-review.zip'))

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('action',choices=['prepare','pilot','build','package']);args=a.parse_args()
    globals()[args.action]()
