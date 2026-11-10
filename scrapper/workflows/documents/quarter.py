"""Quarter inventory assembly, bounded repairs and report exports."""
import csv
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from ...core.access_policy import is_blocked_terms_target
from ...documents.discovery.promotion import url_fiscal_year_conflicts
from ...documents.readiness.quarter import predates_quarter, call_months
from ...db.documents.quarter import repair_period_tags, prioritize, snapshot_inventory
from ...db.documents.quarter import _eligible_companies, _source_identifiers, _exchange_registry, _latest_expectations, _blocked_policy_hosts, _artifact_sources, _artifact_contents, _download_jobs, _quarter_artifacts, _latest_accepted_periods, _accepted_current_companies, _result_filing_coverage

def build_inventory(conn, *, period, call_months=call_months, predates_quarter=predates_quarter,
                    url_fiscal_year_conflicts=url_fiscal_year_conflicts,
                    is_blocked_terms_target=is_blocked_terms_target):
    months=call_months(period)
    companies={r["company_id"]:dict(r) for r in _eligible_companies(conn)}
    identifiers=defaultdict(list)
    for row in _source_identifiers(conn):
        identifiers[row["company_id"]].append(dict(row))
    registry=defaultdict(list)
    for row in _exchange_registry(conn):
        registry[row["company_id"]].append(dict(row))
    expectations=defaultdict(list)
    for row in _latest_expectations(conn, period):
        key=(row["company_id"],row["artifact_type"])
        if not expectations[key]:
            expectations[key].append(dict(row))
    policy_hosts=tuple(r[0] for r in _blocked_policy_hosts(conn))
    sources=defaultdict(list)
    for row in _artifact_sources(conn):
        sources[row["artifact_id"]].append(dict(row))
    contents={r["artifact_id"]:dict(r) for r in _artifact_contents(conn)}
    jobs=defaultdict(list)
    for row in _download_jobs(conn):
        jobs[row["artifact_id"]].append(dict(row))
    artifacts=[]; rows=[]; conflicts=[]; stale_urls=[]; counts=Counter(); by_company=defaultdict(list)
    for row in _quarter_artifacts(conn):
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
    accepted={r['company_id']:r['latest'] for r in _latest_accepted_periods(conn)}
    accepted_current={r['company_id'] for r in _accepted_current_companies(conn, period)}
    filings={r['company_id']:dict(r) for r in _result_filing_coverage(conn, period)}
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


def run_inventory(args, *, build_inventory=build_inventory, repair_period_tags=repair_period_tags, prioritize=prioritize):
    from ...config import assert_configured_writable_targets
    assert_configured_writable_targets(args.output, db_path=args.db)
    mode='rw' if args.prioritize or args.repair_period_tags else 'ro'
    from ...db.database import connect, selected_backend
    conn = (connect(args.db, read_only=mode == 'ro') if selected_backend() == 'postgresql' else
            sqlite3.connect(f'file:{args.db.resolve()}?mode={mode}',uri=True,timeout=30))
    conn.row_factory=sqlite3.Row
    try:
        report=snapshot_inventory(conn,period=args.period,build_inventory=build_inventory)
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
