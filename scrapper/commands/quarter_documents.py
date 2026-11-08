"""Export a deduplicated company/quarter document inventory and recovery list.

Month-labelled call links are CANDIDATES, not proof of the financial period.
--prioritize only raises existing pending/retryable PPT and transcript jobs;
it never retries terminal URLs or overrides source policy.
"""
import argparse
import calendar
import csv
import json
import sqlite3
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

from ..config import DB_PATH
from ..core.access_policy import is_blocked_terms_target
from ..db.repo import utc_now
from ..pipeline.promote_document_links import url_fiscal_year_conflicts


def predates_quarter(labels, period):
    if not labels:
        return False
    try:
        months=[datetime.strptime(label,'%b %Y') for label in labels]
        end=date.fromisoformat(period)
    except ValueError:
        return False
    return all((m.year,m.month)<(end.year,end.month) for m in months)


def call_months(period):
    end=date.fromisoformat(period)
    if end.month not in (3,6,9,12) or end.day!=calendar.monthrange(end.year,end.month)[1]:
        raise ValueError("Period must be a calendar quarter end")
    result=[]
    for offset in (1,2,3):
        total=end.year*12+end.month-1+offset
        result.append(f"{calendar.month_abbr[total%12+1]} {total//12}")
    return tuple(result)


def build_inventory(conn, *, period):
    months=call_months(period)
    companies={r["company_id"]:dict(r) for r in conn.execute("""SELECT s.company_id,c.current_legal_name,
        s.market_cap_crore,s.observed_at_utc FROM screener_collection_scope s
        JOIN companies c ON c.company_id=s.company_id WHERE s.eligibility='eligible'""")}
    identifiers=defaultdict(list)
    for row in conn.execute("""SELECT company_id,source,source_company_id,source_symbol,mapping_status
                              FROM source_identifiers WHERE company_id IS NOT NULL"""):
        identifiers[row["company_id"]].append(dict(row))
    registry=defaultdict(list)
    for row in conn.execute("""SELECT registry.company_id,registry.isin,
                                     registry.canonical_name_raw,
                                     security.exchange,security.exchange_key
                              FROM exchange_company_registry registry
                              JOIN exchange_company_links link
                                ON link.registry_company_id=registry.registry_company_id
                               AND link.is_current=1
                              JOIN exchange_securities security
                                ON security.exchange_security_id=link.exchange_security_id
                             WHERE registry.company_id IS NOT NULL"""):
        registry[row["company_id"]].append(dict(row))
    expectations=defaultdict(list)
    for row in conn.execute("""SELECT expectation.company_id,expectation.artifact_type,
                                     expectation.availability_status,
                                     expectation.last_checked_at_utc,
                                     expectation.next_check_at_utc,
                                     expectation.terminal_reason
                                FROM artifact_expectations expectation
                                JOIN artifact_events event USING(event_id)
                               WHERE event.period_end=?
                            ORDER BY expectation.updated_at_utc DESC, expectation.expectation_id DESC""", (period,)):
        key=(row["company_id"],row["artifact_type"])
        if not expectations[key]:
            expectations[key].append(dict(row))
    policy_hosts=tuple(r[0] for r in conn.execute("SELECT host FROM credit_rating_host_policy WHERE automation_status='BLOCKED_TERMS'"))
    sources=defaultdict(list)
    for row in conn.execute("""SELECT artifact_id,document_url,published_at_utc,
        json_extract(classification_evidence_json,'$.period_label_raw') label FROM artifact_sources"""):
        sources[row["artifact_id"]].append(dict(row))
    contents={r["artifact_id"]:dict(r) for r in conn.execute("""SELECT artifact_id,count(*) content_count,
        max(extraction_status='ok') extracted,count(distinct content_sha256) distinct_hashes
        FROM artifact_contents GROUP BY artifact_id""")}
    jobs=defaultdict(list)
    for row in conn.execute("""SELECT artifact_id,download_job_id,status,last_result_status,last_error,requested_url
                               FROM artifact_download_queue"""):
        jobs[row["artifact_id"]].append(dict(row))
    artifacts=[]; rows=[]; conflicts=[]; stale_urls=[]; counts=Counter(); by_company=defaultdict(list)
    for row in conn.execute("SELECT * FROM document_artifacts WHERE artifact_type IN ('presentation','transcript','recording')"):
        if row["company_id"] not in companies:
            continue
        evidence=sources[row["artifact_id"]]
        labels=sorted({s["label"] for s in evidence if s["label"]})
        if row['period_end']==period and predates_quarter(labels,period):
            conflicts.append({'artifact_id':row['artifact_id'],'company_id':row['company_id'],
                              'old_period_end':period,'call_labels':labels})
            continue
        if row["period_end"]==period:
            basis="tagged_quarter_requires_content_verification"
        elif row["period_end"] is None and set(labels).intersection(months):
            basis="call_month_candidate_requires_content_verification"
        else:
            continue
        if evidence and all(url_fiscal_year_conflicts(s["document_url"],period) for s in evidence):
            stale_urls.append({'artifact_id':row['artifact_id'],'company_id':row['company_id'],
                               'source_urls':sorted({s['document_url'] for s in evidence})})
            continue
        content=contents.get(row["artifact_id"],{})
        queued=jobs[row["artifact_id"]]
        allowed=[s["document_url"] for s in evidence if not is_blocked_terms_target(s["document_url"],additional_hosts=policy_hosts)]
        if content:
            status="downloaded_extracted" if content["extracted"] else "downloaded_needs_extraction"
        elif evidence and not allowed:
            status="policy_excluded"
        elif any(j["status"] in ('PENDING','RETRYABLE_FAILURE','LEASED') for j in queued):
            status="download_pending"
        elif any(j["status"]=='TERMINAL_FAILURE' for j in queued):
            status="alternate_source_needed"
        elif row["artifact_type"]=='recording':
            status="recording_reference_only"
        else:
            status="discovered_not_queued"
        item={"company_id":row["company_id"],"company":companies[row["company_id"]]["current_legal_name"],
              "artifact_id":row["artifact_id"],"artifact_type":row["artifact_type"],"period_basis":basis,
              "call_labels":labels,"status":status,"source_urls":sorted(set(allowed)),
              "jobs":queued,"content":content}
        artifacts.append(item); by_company[row["company_id"]].append(item); counts[status]+=1
    for cid,company in companies.items():
        for kind in ('presentation','transcript','recording'):
            found=[a for a in by_company[cid] if a["artifact_type"]==kind]
            expectation=expectations.get((cid,kind), [None])[0]
            discovery_status=("candidates_found" if found else
                expectation["availability_status"] if expectation else "unknown_needs_discovery")
            rows.append({**company,"period_end":period,"artifact_type":kind,
              "identified_candidates":len(found),"downloaded_candidates":sum(bool(a["content"]) for a in found),
              "extracted_candidates":sum(bool(a["content"].get("extracted")) for a in found),
              "discovery_status":discovery_status,
              "status_basis":"artifact_inventory" if found else "artifact_expectation" if expectation else "no_expectation_or_artifact",
              "expectation_last_checked_at_utc":expectation["last_checked_at_utc"] if expectation else None,
              "expectation_next_check_at_utc":expectation["next_check_at_utc"] if expectation else None,
              "expectation_terminal_reason":expectation["terminal_reason"] if expectation else None,
              "quarter_content_verified":False})
    accepted={r['company_id']:r['latest'] for r in conn.execute("""SELECT company_id,
        max(resolved_source_period) latest FROM accepted_page_versions
        WHERE source='screener' AND reporting_scope='standalone'
          AND resolved_source_period GLOB '[12][0-9][0-9][0-9]-[01][0-9]'
        GROUP BY company_id""")}
    accepted_current={r['company_id'] for r in conn.execute("""SELECT DISTINCT company_id
        FROM accepted_page_versions WHERE source='screener' AND reporting_scope='standalone'
        AND resolved_source_period=?""",(period[:7],))}
    filings={r['company_id']:dict(r) for r in conn.execute("""SELECT state.company_id,
        count(*) filing_count,
        sum(decision.decision_reason LIKE 'possible_same_period_revision:%') revision_review_count,
        sum(CASE WHEN decision.decision IN ('already_accepted','stale') AND EXISTS (
          SELECT 1 FROM exchange_result_filings prior
          WHERE prior.exchange=filing.exchange
            AND prior.exchange_key=filing.exchange_key
            AND prior.reporting_scope=filing.reporting_scope
            AND prior.result_period=filing.result_period
            AND (prior.broadcast_at_utc<filing.broadcast_at_utc OR
              (prior.broadcast_at_utc=filing.broadcast_at_utc
               AND prior.filing_id<filing.filing_id))) THEN 1 ELSE 0 END)
          historical_same_period_review_candidate_count
        FROM exchange_screener_enqueues decision
        JOIN exchange_result_filings filing USING (filing_id)
        JOIN screener_company_collection_state state
          ON state.company_path_normalized=decision.company_path_normalized
         AND state.reporting_scope='standalone'
        WHERE decision.result_period=? GROUP BY state.company_id""",(period[:7],))}
    completeness=[]
    for cid,company in companies.items():
        found=by_company[cid]
        company_identifiers=identifiers.get(cid,[])
        company_registry=registry.get(cid,[])
        has_nse=any(i["source"] == "nse" and i["mapping_status"] == "verified" for i in company_identifiers)
        has_bse=any(i["source"] == "bse" and i["mapping_status"] == "verified" for i in company_identifiers)
        if cid in accepted_current:
            financial_gap_action="covered"
        elif cid in filings:
            financial_gap_action="review_unaccepted_or_revision"
        elif has_nse or has_bse or company_registry:
            financial_gap_action="refresh_exchange_result_feed"
        else:
            financial_gap_action="resolve_exchange_identity"
        entry={"company_id":cid,"company":company['current_legal_name'],
               "market_cap_crore":company['market_cap_crore'],"period_end":period,
               "financial_status":("accepted_standalone" if cid in accepted_current
                   else "filing_seen_not_accepted" if cid in filings
                   else "no_filing_seen_in_database"),
               "latest_accepted_result_period":accepted.get(cid),
               "official_filing_count":filings.get(cid,{}).get('filing_count',0),
               "revision_review_count":filings.get(cid,{}).get('revision_review_count',0),
               "historical_same_period_review_candidate_count":filings.get(cid,{}).get(
                   'historical_same_period_review_candidate_count',0),
               "verified_nse_identifier":has_nse,
               "verified_bse_identifier":has_bse,
               "exchange_registry_isin_count":len({r['isin'] for r in company_registry}),
               "financial_gap_action":financial_gap_action}
        for kind in ('presentation','transcript','recording'):
            matched=[a for a in found if a['artifact_type']==kind]
            expectation=expectations.get((cid,kind), [None])[0]
            entry[f'{kind}_candidate_count']=len(matched)
            entry[f'{kind}_downloaded_candidate_count']=sum(bool(a['content']) for a in matched)
            entry[f'{kind}_extracted_candidate_count']=sum(
                bool(a['content'].get('extracted')) for a in matched)
            entry[f'{kind}_status']=("candidates_found" if matched else
                expectation["availability_status"] if expectation else "unknown_needs_discovery")
            entry[f'{kind}_status_basis']=("artifact_inventory" if matched else
                "artifact_expectation" if expectation else "no_expectation_or_artifact")
        completeness.append(entry)
    return {"period_end":period,"call_month_candidates":months,"eligible_companies":len(companies),
            "artifact_status_counts":dict(counts),"companies":rows,"artifacts":artifacts,
            "completeness":completeness,
            "financial_gap_action_counts":dict(Counter(r["financial_gap_action"] for r in completeness)),
            "document_type_status_counts":{
                kind:dict(Counter(r[f"{kind}_status"] for r in completeness))
                for kind in ('presentation','transcript','recording')},
            "period_conflicts":conflicts,
            "stale_url_candidates":stale_urls,
            "limitations":["Call month is not a verified financial period.",
             "Absent candidate is unknown, not proof that a document was not issued.",
             "Credit ratings are event-driven and are not required for every quarter."]}


def repair_period_tags(conn, inventory):
    changed=0
    with conn:
        for item in inventory['period_conflicts']:
            # A separate primary source may establish the period. Keep such
            # conflicts for review rather than replacing that evidence.
            if conn.execute("SELECT 1 FROM artifact_sources WHERE artifact_id=? AND discovered_from_source!='screener'",(item['artifact_id'],)).fetchone():
                continue
            cursor=conn.execute("UPDATE document_artifacts SET period_end=NULL WHERE artifact_id=? AND period_end=?",
                                (item['artifact_id'],item['old_period_end']))
            if cursor.rowcount:
                conn.execute("""INSERT OR IGNORE INTO artifact_period_corrections
                 (artifact_id,old_period_end,new_period_end,reason,evidence_json,corrected_at_utc)
                 VALUES (?,?,NULL,'call_predates_assigned_quarter',?,?)""",
                 (item['artifact_id'],item['old_period_end'],json.dumps(item,sort_keys=True),utc_now()))
                changed+=1
    return changed


def prioritize(conn, inventory):
    selected=[]
    for a in inventory["artifacts"]:
        if a["content"] or a["artifact_type"] not in ('presentation','transcript'):
            continue
        for job in a["jobs"]:
            if job["status"] in ('PENDING','RETRYABLE_FAILURE') and not is_blocked_terms_target(job["requested_url"]):
                selected.append(job["download_job_id"])
    with conn:
        conn.executemany("""UPDATE artifact_download_queue SET priority=min(priority,10)
           WHERE download_job_id=? AND status IN ('PENDING','RETRYABLE_FAILURE')""",[(i,) for i in selected])
    return len(selected)


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--db',type=Path,default=DB_PATH)
    p.add_argument('--period',required=True); p.add_argument('--output',type=Path,required=True)
    p.add_argument('--prioritize',action='store_true'); p.add_argument('--repair-period-tags',action='store_true')
    p.add_argument('--apply',action='store_true'); args=p.parse_args()
    if (args.prioritize or args.repair_period_tags) and not args.apply:
        p.error('Mutating options require --apply')
    from ..config import assert_configured_writable_targets
    assert_configured_writable_targets(args.output, db_path=args.db)
    mode='rw' if args.prioritize or args.repair_period_tags else 'ro'
    conn=sqlite3.connect(f'file:{args.db.resolve()}?mode={mode}',uri=True,timeout=30); conn.row_factory=sqlite3.Row
    try:
        conn.execute('BEGIN'); report=build_inventory(conn,period=args.period); conn.rollback()
        if args.repair_period_tags:
            report['corrected_period_tags']=repair_period_tags(conn,report)
        if args.prioritize:
            report['prioritized_jobs']=prioritize(conn,report)
        args.output.mkdir(parents=True,exist_ok=True)
        (args.output/'inventory.json').write_text(json.dumps(report,indent=2,sort_keys=True)+'\n')
        with (args.output/'company-quarter-coverage.csv').open('w',newline='') as f:
            if report['companies']:
                writer=csv.DictWriter(f,fieldnames=list(report['companies'][0]));writer.writeheader();writer.writerows(report['companies'])
        with (args.output/'company-quarter-completeness.csv').open('w',newline='') as f:
            if report['completeness']:
                writer=csv.DictWriter(f,fieldnames=list(report['completeness'][0]));writer.writeheader();writer.writerows(report['completeness'])
        gaps=[r for r in report['completeness'] if r['financial_status'] != 'accepted_standalone']
        with (args.output/'financial-gap-ledger.csv').open('w',newline='') as f:
            if gaps:
                writer=csv.DictWriter(f,fieldnames=list(gaps[0])); writer.writeheader(); writer.writerows(gaps)
        print(json.dumps({k:v for k,v in report.items() if k not in ('artifacts','companies','completeness')},sort_keys=True))
    finally:
        conn.close()


if __name__=='__main__':
    main()
