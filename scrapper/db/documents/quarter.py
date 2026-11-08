"""Quarter inventory reads and bounded period/priority repairs."""
import json
from ..repo import utc_now
from ...core.access_policy import is_blocked_terms_target

def _eligible_companies(conn):
    return conn.execute("SELECT s.company_id,c.current_legal_name,\n        s.market_cap_crore,s.observed_at_utc FROM screener_collection_scope s\n        JOIN companies c ON c.company_id=s.company_id WHERE s.eligibility='eligible'")

def _source_identifiers(conn):
    return conn.execute('SELECT company_id,source,source_company_id,source_symbol,mapping_status\n                              FROM source_identifiers WHERE company_id IS NOT NULL')

def _exchange_registry(conn):
    return conn.execute('SELECT registry.company_id,registry.isin,\n                                     registry.canonical_name_raw,\n                                     security.exchange,security.exchange_key\n                              FROM exchange_company_registry registry\n                              JOIN exchange_company_links link\n                                ON link.registry_company_id=registry.registry_company_id\n                               AND link.is_current=1\n                              JOIN exchange_securities security\n                                ON security.exchange_security_id=link.exchange_security_id\n                             WHERE registry.company_id IS NOT NULL')

def _latest_expectations(conn, period):
    return conn.execute('SELECT expectation.company_id,expectation.artifact_type,\n                                     expectation.availability_status,\n                                     expectation.last_checked_at_utc,\n                                     expectation.next_check_at_utc,\n                                     expectation.terminal_reason\n                                FROM artifact_expectations expectation\n                                JOIN artifact_events event USING(event_id)\n                               WHERE event.period_end=?\n                            ORDER BY expectation.updated_at_utc DESC, expectation.expectation_id DESC', (period,))

def _blocked_policy_hosts(conn):
    return conn.execute("SELECT host FROM credit_rating_host_policy WHERE automation_status='BLOCKED_TERMS'")

def _artifact_sources(conn):
    return conn.execute("SELECT artifact_id,document_url,published_at_utc,\n        json_extract(classification_evidence_json,'$.period_label_raw') label FROM artifact_sources")

def _artifact_contents(conn):
    return conn.execute("SELECT artifact_id,count(*) content_count,\n        max(extraction_status='ok') extracted,count(distinct content_sha256) distinct_hashes\n        FROM artifact_contents GROUP BY artifact_id")

def _download_jobs(conn):
    return conn.execute('SELECT artifact_id,download_job_id,status,last_result_status,last_error,requested_url\n                               FROM artifact_download_queue')

def _quarter_artifacts(conn):
    return conn.execute("SELECT * FROM document_artifacts WHERE artifact_type IN ('presentation','transcript','recording')")

def _latest_accepted_periods(conn):
    return conn.execute("SELECT company_id,\n        max(resolved_source_period) latest FROM accepted_page_versions\n        WHERE source='screener' AND reporting_scope='standalone'\n          AND resolved_source_period GLOB '[12][0-9][0-9][0-9]-[01][0-9]'\n        GROUP BY company_id")

def _accepted_current_companies(conn, period):
    return conn.execute("SELECT DISTINCT company_id\n        FROM accepted_page_versions WHERE source='screener' AND reporting_scope='standalone'\n        AND resolved_source_period=?", (period[:7],))

def _result_filing_coverage(conn, period):
    return conn.execute("SELECT state.company_id,\n        count(*) filing_count,\n        sum(decision.decision_reason LIKE 'possible_same_period_revision:%') revision_review_count,\n        sum(CASE WHEN decision.decision IN ('already_accepted','stale') AND EXISTS (\n          SELECT 1 FROM exchange_result_filings prior\n          WHERE prior.exchange=filing.exchange\n            AND prior.exchange_key=filing.exchange_key\n            AND prior.reporting_scope=filing.reporting_scope\n            AND prior.result_period=filing.result_period\n            AND (prior.broadcast_at_utc<filing.broadcast_at_utc OR\n              (prior.broadcast_at_utc=filing.broadcast_at_utc\n               AND prior.filing_id<filing.filing_id))) THEN 1 ELSE 0 END)\n          historical_same_period_review_candidate_count\n        FROM exchange_screener_enqueues decision\n        JOIN exchange_result_filings filing USING (filing_id)\n        JOIN screener_company_collection_state state\n          ON state.company_path_normalized=decision.company_path_normalized\n         AND state.reporting_scope='standalone'\n        WHERE decision.result_period=? GROUP BY state.company_id", (period[:7],))

def repair_period_tags(conn, inventory, *, utc_now=utc_now):
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


def prioritize(conn, inventory, *, is_blocked_terms_target=is_blocked_terms_target):
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


def snapshot_inventory(conn, *, period, build_inventory):
    conn.execute('BEGIN')
    report = build_inventory(conn, period=period)
    conn.rollback()
    return report
