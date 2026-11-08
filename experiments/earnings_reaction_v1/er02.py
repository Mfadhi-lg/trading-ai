"""Bounded ER-02 continuation: twelve events, retained evidence only, no HTTP client."""
from __future__ import annotations
import argparse
import collections
import csv
import difflib
import hashlib
import json
import zipfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from . import event_isolation as er

OUT=er.OUT/'er02'
PILOT=[('FY2026-Q4',132,'Original and explicitly described reporting-type correction; retained next-period feed'),
       ('FY2026-Q4',760,'Strong next-period feed plus explicit blank-figures correction; scope and parent checks'),
       ('FY2026-Q4',542,'Intraday original candidate; two scopes and retained next-period feed'),
       ('FY2026-Q4',912,'Pre-open clock-time original candidates; two scopes and retained next-period feed'),
       ('FY2026-Q4',84,'Weekend-dated original candidate; avoids inferring a trading session'),
       ('FY2026-Q4',6,'Banking case, two original scopes, retained next-period dissemination fields'),
       ('FY2027-Q1',223,'Retained start and September BSE filing bytes plus two-scope NSE start records'),
       ('FY2027-Q1',225,'Retained start and September filing bytes; issuer fiscal-label convention needs review'),
       ('FY2027-Q1',261,'Retained start and September filing bytes; both reporting scopes'),
       ('FY2027-Q1',267,'Retained start and September filing bytes; both reporting scopes'),
       ('FY2027-Q1',894,'Retained start and September filing bytes; both reporting scopes'),
       ('FY2027-Q1',142,'Single-scope start and September filing bytes; simpler reconciliation control')]


def freeze_file(path):
    digest=er.sha(path);target=OUT/'evidence'/digest/path.name
    target.parent.mkdir(parents=True,exist_ok=True)
    if not target.exists():target.write_bytes(path.read_bytes())
    return dict(body_path=str(target.relative_to(OUT)),sha256=digest,original_path=str(path))


def resolve_paths(path):
    """Only documented mount substitutions, then hash verification by caller."""
    p=Path(path);candidates=[p]
    if path.startswith('/data/'):
        candidates.append(er.ROOT/'scrapper/store'/path.removeprefix('/data/'))
    if path.startswith('/documents/'):
        candidates.append(Path('/Volumes/My Passport/passport/trading-ai')/path.removeprefix('/documents/'))
    return candidates


def prepare():
    OUT.mkdir(parents=True,exist_ok=True)
    source=json.loads((er.OUT/'inputs.json').read_text())
    wanted={(q,c) for q,c,_ in PILOT};ids={c for _,c,_ in PILOT}
    p={k:source[k] for k in ['version','cutoff','cutoff_basis','document_artifacts_schema']}
    p['cohort']=[r for r in source['cohort'] if (r['quarter'],r['canonical_company_id']) in wanted]
    p['companies']=[r for r in source['companies'] if r['company_id'] in ids]
    p['identifiers']=[r for r in source['identifiers'] if r['company_id'] in ids]
    p['classifications']=[] # Boundary verification does not depend on subsector data.
    p['filings']=[r for r in source['filings'] if r['canonical_company_id'] in ids]
    p['parsed_filings']=[r for r in source['parsed_filings'] if r['canonical_company_id'] in ids]
    p['links']=[r for r in source['links'] if r['company_id'] in ids]
    secids={r['exchange_security_id'] for r in p['links']}
    p['securities']=[r for r in source['securities'] if r['exchange_security_id'] in secids]
    p['versions']=[r for r in source['versions'] if r['exchange_security_id'] in secids]
    p['candle_datasets']=[];p['original_disclosure_review']={}
    snapids={r['snapshot_id'] for r in p['filings']}
    p['sources']=[r for r in source['sources'] if r['snapshot_id'] in snapids]
    # Freeze only the selected evidence and original ER-01 coverage rows.
    refs={r['source']['retained_path']:r['source'] for r in p['parsed_filings']}
    for ref in refs.values():freeze_file(er.OUT/ref['retained_path'])
    baseline={str(f.relative_to(er.OUT)):er.sha(f) for f in er.OUT.iterdir() if f.is_file()}
    er.dump(OUT/'ER01_PRESERVATION_BEFORE.json',baseline)
    db=er.ro(er.DB);before=[er.DB.stat().st_size,er.DB.stat().st_mtime_ns]
    receipt_metadata=[];recoveries=[]
    for s in p['sources']:
        fetch=db.execute('select * from fetch_observations where fetch_id=?',(s['fetch_id'],)).fetchone()
        if fetch:
            fetch=dict(fetch)
            receipt_metadata.append({k:fetch.get(k) for k in ['fetch_id','source','requested_url','final_url','fetched_at_utc','http_status','blob_id','fetch_status','resource_kind']})
        candidates=resolve_paths(s['local_path'])
        recovered=next((path for path in candidates if path.is_file() and er.sha(path)==s['content_sha256']),None)
        recoveries.append(dict(kind='filing_feed',snapshot_id=s['snapshot_id'],expected_sha256=s['content_sha256'],
            checked_paths=[str(x) for x in candidates],recovered=freeze_file(recovered) if recovered else None))
    earlier=[];attachments=[];links=[]
    # Company-indexed queries, maximum 100 records/company/category. No cohort-wide scan.
    for cid in sorted(ids):
        pages=er.rows(db,'''select p.*,b.local_path,b.content_sha256 from artifact_discovery_pages p
            join response_blobs b using(blob_id) where p.company_id=? and p.discovered_from_source in ('nse','bse')
            order by p.discovery_page_id limit 100''',(cid,))
        for page in pages:
            paths=resolve_paths(page['local_path'])
            recovered=next((x for x in paths if x.is_file() and er.sha(x)==page['content_sha256']),None)
            earlier.append(dict(**page,checked_paths=[str(x) for x in paths],recovered=freeze_file(recovered) if recovered else None,
                applicability='URL query dates require review; older-period search cannot establish 2026 earliest release'))
        docs=er.rows(db,'''select d.company_id,d.artifact_id,d.artifact_type,d.title_raw,d.period_end,
            a.content_id,b.local_path,b.content_sha256 from document_artifacts d
            join artifact_contents a using(artifact_id) join response_blobs b using(blob_id)
            where d.company_id=? and d.artifact_type='presentation' order by d.artifact_id limit 100''',(cid,))
        for doc in docs:
            paths=resolve_paths(doc['local_path'])
            # Existence is enough to inventory non-results presentation candidates;
            # do not hash/copy unrelated PDFs or reclassify them as original results.
            doc.update(existing_paths=[str(x) for x in paths if x.is_file()],boundary_use='EXCLUDED_PRESENTATION_NOT_ORIGINAL_RESULTS')
            attachments.append(doc)
        links.extend(er.rows(db,'''select v.company_id,l.document_link_id,l.document_type,l.title_raw,l.source_url,l.source_published_at_utc,
            l.availability_basis from accepted_page_versions v join document_links l using(parse_id)
            where v.company_id=? and (lower(l.document_type) like '%result%' or lower(l.title_raw) like '%financial result%')
            order by l.document_link_id limit 100''',(cid,)))
    # Exact selected feed-hash lookup in existing snapshot blob registries.
    # Missing receipt is distinct from a content hash match.
    blob_matches=[]
    for path in sorted((er.ROOT/'artifacts/acquisition-readiness').rglob('scraper.db')):
        conn=er.ro(path)
        for ref in refs.values():
            found=er.rows(conn,'select blob_id,local_path,content_sha256 from response_blobs where content_sha256=?',(ref['sha256'],))
            if found:blob_matches.append(dict(database=str(path),sha256=ref['sha256'],records=found))
        conn.close()
    db.close();assert before==[er.DB.stat().st_size,er.DB.stat().st_mtime_ns]
    selected=[]
    for q,cid,reason in PILOT:
        company=next(r for r in p['companies'] if r['company_id']==cid)
        symbol=next(r['symbol_raw'] for r in p['cohort'] if r['quarter']==q and r['canonical_company_id']==cid)
        bse=sorted({s['exchange_key'] for s in p['securities'] if s['exchange']=='BSE' and any(l['company_id']==cid and l['exchange_security_id']==s['exchange_security_id'] for l in p['links'])})
        selected.append(dict(event_id=f'{q}:{cid}',quarter=q,company_id=cid,company_name=company['current_legal_name'],nse_symbol=symbol,bse_scrip_codes=bse,selection_reason=reason))
    # Review files accept substantiated actual disclosures, never booleans.
    er.dump(OUT/'PILOT_INPUTS.json',p)
    er.dump(OUT/'PILOT_SELECTION.json',selected)
    er.dump(OUT/'RECOVERY_AUDIT.json',dict(database_size_mtime_unchanged=True,feed_recovery=recoveries,
        acquisition_receipt_metadata=receipt_metadata,retained_earlier_exchange_pages=earlier,
        presentation_candidates=attachments,accepted_results_links=links,standalone_feed_registry_matches=blob_matches,
        blob_registry_databases_checked=[str(path) for path in sorted((er.ROOT/'artifacts/acquisition-readiness').rglob('scraper.db'))],
        limits='12 companies only; indexed company queries capped at 100; absent or non-applicable evidence is not publication absence'))
    reviews=OUT/'DISCLOSURE_REVIEWS.json'
    if not reviews.exists():er.dump(reviews,{r['event_id']:{} for r in selected})
    with (er.OUT/'DATA_COVERAGE.csv').open() as handle:
        coverage=[r for r in csv.DictReader(handle) if r['event_id'] in {x['event_id'] for x in selected}]
    er.write_csv('PRIOR_MARKET_READINESS.csv',coverage,out=OUT)
    print(er.J(dict(selected_events=len(selected),stored_filings=len(p['filings']),parsed_rows=len(p['parsed_filings']),earlier_pages=len(earlier),result_links=len(links))))


def review_evidence(p,reviews,ledger):
    """Project receipt-validated official review rows alongside legacy feed rows.

    Period/scope membership stays a candidate annotation until actual content
    and earliest release pass. No announcement timestamp is inferred here.
    """
    from .verification import _json_pointer
    result=er.evidence_records(p)
    seen={(r['exchange'],r['native_announcement_id']) for r in result if r['native_announcement_id']}
    for event in ledger:
        details=json.loads(event['verification_details'])
        for label,end in [('start',event['period_end']),('next',event['next_period_end'])]:
            disclosure=reviews.get(event['event_id'],{}).get(label)
            if not disclosure or not details[label]['candidate']:continue
            identity=(disclosure['exchange'],str(disclosure['native_announcement_id']))
            if identity in seen:continue
            seen.add(identity)
            ref=disclosure['announcement']
            row=_json_pointer(json.loads((OUT/ref['body_path']).read_bytes()),disclosure['row_pointer'])
            receipt=json.loads((OUT/ref['receipt_path']).read_text())
            published=details[label]['publication_at_ist'] if details[label]['timestamp_verified'] else None
            source=dict(retained_path=ref['body_path'],original_path=receipt['requested_url'],sha256=ref['sha256'])
            result.append(dict(record_id='official:'+identity[0]+':'+identity[1],company_id=int(event['company_id']),company_candidates=[int(event['company_id'])],
                exchange=identity[0],exchange_key=disclosure['exchange_key'],isin=row.get('sm_isin'),period_end=end,scope='UNRESOLVED_ATTACHMENT_CONTENT',
                filing_key=None,raw=row,stored_broadcast_at_utc=None,evidence_locator=ref['body_path']+'#'+disclosure['row_pointer'],
                source_blob_locator=ref['body_path'],source_hash=ref['sha256'],source=source,row_position=disclosure['row_pointer'],source_capture=receipt['captured_at'],
                filing_dissemination_ist=published,retained_field_dissemination_ist=published,
                dissemination_check='receipt_bound_official_record' if published else 'TIMESTAMP_UNRESOLVED',
                raw_dissemination=row.get(disclosure['public_timestamp_field']),raw_broadcast=row.get('an_dt'),raw_revision=None,
                filing_type=row.get('desc','UNSPECIFIED'),source_url=row.get(disclosure.get('attachment_url_field','')),
                relationship_status='EARLIEST_AND_CORRECTION_REVIEW_UNRESOLVED' if not details[label]['earliest_verified'] else 'EARLIEST_VERIFIED',
                original_results_timestamp_ist=details[label]['original_release_at_ist'],publication_confidence='PUBLIC_DISSEMINATION_VERIFIED_EARLIEST_UNRESOLVED' if published else 'PUBLIC_DISSEMINATION_UNVERIFIED',
                session_status='CALENDAR_UNVERIFIED',clock_phase=er.phase(datetime.fromisoformat(published)) if published else 'UNKNOWN',
                clock_phase_basis='VERIFIED_OFFICIAL_DISSEMINATION' if published else 'UNKNOWN',identity_status='CANONICAL_ISSUER_MATCH',
                native_announcement_id=identity[1],same_filing_identity=identity[0]+'|native:'+identity[1]))
    return result


def build():
    p=json.loads((OUT/'PILOT_INPUTS.json').read_text())
    reviews=json.loads((OUT/'DISCLOSURE_REVIEWS.json').read_text())
    assert len(p['cohort'])==12 and len({r['canonical_company_id'] for r in p['cohort']})==12
    er.build(payload=p,output_dir=OUT,verification_reviews=reviews,evidence_root=OUT)
    with (OUT/'EVENT_LEDGER.csv').open() as handle:ledger=list(csv.DictReader(handle))
    selected={r['event_id']:r for r in json.loads((OUT/'PILOT_SELECTION.json').read_text())}
    evidence=review_evidence(p,reviews,ledger)
    for row in ledger:
        for label,end in [('start',row['period_end']),('next',row['next_period_end'])]:
            found=[r for r in evidence if str(r['company_id'])==row['company_id'] and r['period_end']==end]
            row[label+'_evidence_ids']=er.J([r['record_id'] for r in found])
            row[label+'_filing_count']=str(len({r['same_filing_identity'] for r in found}))
            row['retained_'+label+'_filing_disseminations']=er.J(sorted({r['retained_field_dissemination_ist'] for r in found if r['retained_field_dissemination_ist']}))
    er.write_csv('EVENT_LEDGER.csv',ledger,out=OUT)
    flat=[{**{k:v for k,v in r.items() if k not in ('source','raw')},'retained_evidence_path':r['source']['retained_path'] if r['source'] else None,'raw_row_json':r['raw']} for r in evidence]
    er.write_csv('EXCHANGE_EVIDENCE.csv',flat,out=OUT)
    summary=json.loads((OUT/'SUMMARY.json').read_text());summary['evidence_records']=len(evidence)
    summary['events_with_retained_start_filing_field']=sum(bool(json.loads(r['retained_start_filing_disseminations'])) for r in ledger)
    summary['events_with_retained_next_filing_field']=sum(bool(json.loads(r['retained_next_filing_disseminations'])) for r in ledger)
    summary['verified_start_announcement_timestamps']=sum(r['start_timestamp_stage']=='VERIFIED' for r in ledger)
    summary['verified_next_announcement_timestamps']=sum(r['next_timestamp_stage']=='VERIFIED' for r in ledger)
    er.dump(OUT/'SUMMARY.json',summary)
    table=[];missing=[]
    for row in ledger:
        table.append(dict(**selected[row['event_id']],calendar_status='UNVERIFIED_RETAINED_SESSION_PROPOSALS_ONLY',
            instrument_continuity_status=row['historical_instrument_status'],candle_coverage='NO_RETAINED_COHORT_DAILY_OR_INTRADAY',**{k:row[k] for k in ['candidate_filing_stage','start_timestamp_stage','earliest_start_stage','next_timestamp_stage','next_disclosure_stage','event_window_stage','boundary_status','market_data_status','announcement_at_ist','next_announcement_at_ist','window_start_inclusive_ist','window_end_exclusive_ist','verification_details']}))
        details=json.loads(row['verification_details'])
        for which in ('start','next'):
            if not details[which]['earliest_verified']:
                missing.append(dict(event_id=row['event_id'],boundary=which,company_id=row['company_id'],
                    reasons=details[which]['reasons'],required='Receipt-bound original official announcement, linked actual-results attachment, explicit public timestamp, complete earlier-announcement search on both issuer venues; correction parent adjudication'))
    # Keep candidate values visibly separate from verified publication columns.
    for item in table:
        period,nxt=er.PERIODS[item['quarter']]
        for label,end in [('start',period),('next',nxt)]:
            found=[r for r in evidence if r['company_id']==item['company_id'] and r['period_end']==end]
            item[label+'_evidence_ids']=[r['record_id'] for r in found]
            item[label+'_raw_candidate_times']=sorted({r['retained_field_dissemination_ist'] or r['raw_dissemination'] or r['raw_broadcast'] for r in found if r['retained_field_dissemination_ist'] or r['raw_dissemination'] or r['raw_broadcast']})
    er.write_csv('PILOT_VERIFICATION.csv',table,out=OUT)
    er.write_csv('TIMESTAMP_EVIDENCE.csv',[dict(record_id=r['record_id'],company_id=r['company_id'],period_end=r['period_end'],
        exchange=r['exchange'],exchange_key=r['exchange_key'],filing_key=r['filing_key'],native_announcement_id=r['native_announcement_id'],
        source_url=r['source_url'],evidence_locator=r['evidence_locator'],expected_source_hash=r['source_hash'],
        source_blob_locator=r['source_blob_locator'],raw_dissemination=r['raw_dissemination'],raw_broadcast=r['raw_broadcast'],
        retained_field_dissemination_ist=r['retained_field_dissemination_ist'],verified_publication_at_ist=r['filing_dissemination_ist'],
        status=r['publication_confidence'],relationship=r['relationship_status']) for r in evidence],out=OUT)
    for item in missing:
        q=selected[item['event_id']]['quarter'];end=er.PERIODS[q][0 if item['boundary']=='start' else 1]
        found=[r for r in evidence if str(r['company_id'])==str(item['company_id']) and r['period_end']==end]
        item.update(period_end=end,candidate_record_ids=[r['record_id'] for r in found],
            official_filing_links=sorted({r['source_url'] for r in found if r['source_url']}),
            missing_snapshot_blob_hashes=sorted({r['source_hash'] for r in found if not r['source']}),
            cause=('Exchange dissemination verified; actual-results attachment, earliest-search completeness and cross-exchange reconciliation remain unresolved' if json.loads(next(r['verification_details'] for r in ledger if r['event_id']==item['event_id']))[item['boundary']]['timestamp_verified'] else 'Original actual-results attachment and complete earlier-announcement searches unavailable; retained timestamp field origin unresolved'),
            requests=[item['event_id']+':'+item['boundary']+':'+exchange for exchange in ['NSE','BSE']])
    continuation_path=OUT/'NSE_CONTINUATION_MANIFEST.json'
    continuation=json.loads(continuation_path.read_text()) if continuation_path.is_file() else {'requests':[]}
    for item in missing:
        detail=json.loads(next(r['verification_details'] for r in ledger if r['event_id']==item['event_id']))[item['boundary']]
        item['public_timestamp_status']='VERIFIED' if detail['timestamp_verified'] else 'UNRESOLVED'
        item['proposed_continuation_request_ids']=[r['request_id'] for r in continuation['requests'] if r['event_id']==item['event_id'] and r['boundary']==item['boundary']]
    er.write_csv('MISSING_EVIDENCE.csv',missing,list(missing[0]) if missing else ['event_id','boundary','company_id','reasons','required'],out=OUT)
    before=json.loads((OUT/'ER01_PRESERVATION_BEFORE.json').read_text())
    available={name:er.sha(er.OUT/name) for name in before if (er.OUT/name).is_file()}
    assert all(before[name]==digest for name,digest in available.items())
    er.dump(OUT/'ER01_PRESERVATION_AFTER.json',dict(unchanged=True,original_files_available=len(available),original_files_expected=len(before),files=available))


def manifest():
    """Exact discovery GETs only. No execution facility, attachments or pagination authority."""
    p=json.loads((OUT/'PILOT_INPUTS.json').read_text())
    selected=json.loads((OUT/'PILOT_SELECTION.json').read_text());requests=[]
    for event in selected:
        start,nextperiod=er.PERIODS[event['quarter']]
        for role,period in [('start',start),('next',nextperiod)]:
            candidates=[r for r in p['filings'] if r['canonical_company_id']==event['company_id'] and r['period_end_date']==period and r['exchange']=='NSE' ]
            # Include the latest correction candidate in the date scope; never infer a release from it.
            if candidates:
                last=max(datetime.fromisoformat(r['broadcast_at_utc']).astimezone(er.IST).date() for r in candidates)
            else:
                candidates=[r for r in p['parsed_filings'] if r['canonical_company_id']==event['company_id'] and r['period_end_date']==period]
                if not candidates:raise ValueError('pilot boundary has no date-scoping candidate')
                last=max(datetime.fromisoformat(r['broadcast_at_utc']).astimezone(er.IST).date() for r in candidates)
            first=datetime.fromisoformat(period).date()
            for exchange in ['NSE','BSE']:
                if exchange=='NSE':
                    params=dict(index='equities',symbol=event['nse_symbol'],from_date=first.strftime('%d-%m-%Y'),to_date=last.strftime('%d-%m-%Y'))
                    url='https://www.nseindia.com/api/corporate-announcements?'+urlencode(params)
                else:
                    if len(event['bse_scrip_codes'])!=1:raise ValueError('BSE identity must resolve exactly for manifest')
                    params=dict(pageno=1,strCat='-1',strPrevDate=first.strftime('%Y%m%d'),strScrip=event['bse_scrip_codes'][0],strSearch='P',strToDate=last.strftime('%Y%m%d'),strType='C')
                    url='https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w?'+urlencode(params)
                requests.append(dict(request_id=f'{event["event_id"]}:{role}:{exchange}',event_id=event['event_id'],boundary=role,exchange=exchange,
                    method='GET',url=url,from_date=first.isoformat(),through_date=last.isoformat(),purpose='Original-results and earlier-announcement discovery; includes board outcomes and corrections',
                    expected_type='application/json',max_attempts=1,max_response_bytes=4_000_000,timeout_seconds=30,
                    tranche=1 if event['company_id'] in (132,142) else 2))
    payload=dict(version='ER-02-official-discovery-v1',approval_status='AWAITING_OWNER_APPROVAL_NO_REQUESTS_EXECUTED',
        scope='12 events / 24 quarter boundaries, exact issuer/date queries on both exchanges; no market data',
        request_count=len(requests),initial_tranche_request_count=sum(r['tranche']==1 for r in requests),
        execution_limits=dict(serial=True,max_attempts_per_request=1,retries=0,follow_redirects=False,credentials=False,
            stop_on=[401,403,429],no_browser_bypass=True,automatic_pagination=False,automatic_attachment_fetch=False),
        completeness_warning='Page 1 may be incomplete. Any additional page or newly discovered attachment needs a new exact owner-approved manifest. This initial discovery manifest alone does not guarantee verified boundaries.',requests=requests)
    er.dump(OUT/'OFFICIAL_RETRIEVAL_MANIFEST.json',payload)
    (OUT/'OFFICIAL_RETRIEVAL_MANIFEST.sha256').write_text(er.sha(OUT/'OFFICIAL_RETRIEVAL_MANIFEST.json')+'\n')
    er.write_csv('OFFICIAL_RETRIEVAL_REQUESTS.csv',requests,out=OUT)
    print(er.J(dict(requests=len(requests),initial_tranche=payload['initial_tranche_request_count'],sha256=er.sha(OUT/'OFFICIAL_RETRIEVAL_MANIFEST.json'))))


def package():
    paths=[p for p in OUT.rglob('*') if p.is_file() and p.suffix!='.zip' and p.name!='SHA256SUMS']
    names=['event_isolation.py','verification.py','events.py','er02.py','retrieve_er02.py']
    paths += [Path(__file__).parent/n for n in names]
    paths += [Path(__file__).parent/'tests/test_er02.py',Path(__file__).parent/'tests/test_retrieve_er02.py']
    paths += [er.ROOT/x for x in ['scrapper/sources/exchange/result_filings.py','scrapper/sources/exchange/tabular.py','scrapper/sources/exchange/security_master.py','scrapper/core/nums.py']]
    # Frozen pilot refers to original ER-01 source locators; include only those used.
    p=json.loads((OUT/'PILOT_INPUTS.json').read_text())
    paths += [er.OUT/r['source']['retained_path'] for r in p['parsed_filings']]
    paths=sorted(set(paths))
    (OUT/'SHA256SUMS').write_text(''.join(f'{er.sha(p)}  {p.relative_to(er.ROOT)}\n' for p in paths))
    paths.append(OUT/'SHA256SUMS')
    with zipfile.ZipFile(OUT/'ER-02-review.zip','w',compression=zipfile.ZIP_DEFLATED) as z:
        for path in paths:
            info=zipfile.ZipInfo(str(path.relative_to(er.ROOT)),date_time=(2026,11,2,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            z.writestr(info,path.read_bytes())
    print(str(OUT/'ER-02-review.zip'))

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('action',choices=['prepare','build','manifest','package']);args=a.parse_args();globals()[args.action]()
