"""Quarter-scoped acquisition observations; bounded SQL, no production writes."""
import sqlite3
import time
from postgresql_runtime import query_deadline
from scrapper.db.repository_backend import is_postgresql, statement
from reporting_quarters import normalize, predicate, label, default_quarter


def options(conn):
    # Only explicit canonical period fields. No timestamp-based inference.
    # Offer recent reporting quarters even when discovery has no rows yet.
    year,month=map(int,default_quarter().split('-'))
    values=set()
    for _ in range(8):
        values.add(f'{year:04d}-{month:02d}')
        month-=3
        if month<1:year-=1;month=12
    for table, field in [('screener_company_queue','result_period'),
                         ('exchange_result_filings','result_period'),
                         ('document_artifacts','period_end'), ('artifact_events','period_end')]:
        for row in conn.execute(f'SELECT DISTINCT {field} FROM {table} WHERE {field} IS NOT NULL'):
            try:
                values.add(normalize(row[0][:7]))
            except ValueError:
                pass  # Sentinel/non-period queue metadata is not a quarter.
    for row in conn.execute("SELECT DISTINCT f.period_end FROM accepted_page_versions p JOIN financial_observations f ON f.parse_id=p.parse_id WHERE f.section_code='quarters' AND f.period_type='quarter' AND f.row_position=(SELECT min(h.row_position) FROM financial_observations h WHERE h.parse_id=p.parse_id AND h.section_code='quarters')"):
        try:
            values.add(normalize(row[0][:7]))
        except (ValueError, TypeError):
            pass
    return [{'value':'all','label':label('all')}] + [
        {'value':v,'label':label(v)+' (March FY convention)'} for v in sorted(values, reverse=True)] + [
        {'value':'unknown','label':label('unknown')}]


def monitor(conn, quarter, term='', offset=0, company_id=0, export=False):
    started = time.monotonic()
    quarter = normalize(quarter)
    conn.row_factory = sqlite3.Row
    def rows(sql, args=()):
        deadline = time.monotonic()+3
        query_deadline(conn,deadline,5000)
        return [dict(r) for r in conn.execute(sql,args)]
    fq, fa = predicate('q.result_period',quarter)
    ap, aa = predicate('a.period_end',quarter)
    ep, ea = predicate('e.period_end',quarter)
    fp, fpa = predicate('f.result_period',quarter)
    # The accepted page contains a matrix of reporting quarters. Restrict to
    # the canonical first quarterly row to avoid scanning every financial cell.
    accepted = '''SELECT DISTINCT p.company_id,p.reporting_scope,p.accepted_at_utc,p.parse_id
        FROM accepted_page_versions p JOIN financial_observations f ON f.parse_id=p.parse_id
        WHERE f.section_code='quarters' AND f.period_type='quarter'
        AND f.row_position=(SELECT min(h.row_position) FROM financial_observations h
            WHERE h.parse_id=p.parse_id AND h.section_code='quarters') AND '''
    af, afa = predicate('f.period_end',quarter)
    result = {'quarter':quarter, 'label':label(quarter), 'options': options(conn),
              'period_semantics':'Explicit reporting period. FY labels use March convention; company artifact fiscal metadata is shown separately.',
              'verification':'Stored validated content and hashes; monitoring does not rehash bytes.',
              'discovery_coverage':'Incomplete/unavailable exchange discovery; absence is not proof of non-publication.',
              'queues':{}}
    result['queues']['screener_company_queue'] = rows('SELECT q.status state,count(*) n FROM screener_company_queue q WHERE '+fq+' GROUP BY q.status',fa)
    for name in ('artifact_download_queue','credit_rating_download_queue'):
        result['queues'][name] = rows(f'SELECT q.status state,count(*) n FROM {name} q JOIN document_artifacts a USING(artifact_id) WHERE '+ap+' GROUP BY q.status',aa)
    result['documents'] = rows('SELECT a.acquisition_status state,count(*) n FROM document_artifacts a WHERE '+ap+' GROUP BY 1',aa)
    result['processing'] = rows('''SELECT c.extraction_status state,count(DISTINCT c.content_id) n
        FROM document_artifacts a JOIN artifact_contents c ON c.content_id=coalesce(a.preferred_content_id,
          (SELECT cc.content_id FROM artifact_contents cc WHERE cc.artifact_id=a.artifact_id ORDER BY cc.content_id DESC LIMIT 1))
        WHERE '''+ap+' GROUP BY 1',aa)
    result['discovery'] = rows('SELECT x.availability_status state,count(*) n FROM artifact_expectations x JOIN artifact_events e USING(event_id) WHERE '+ep+' GROUP BY 1',ea)
    result['accepted'] = rows('SELECT reporting_scope,count(DISTINCT company_id) companies,max(accepted_at_utc) last_accepted FROM ('+accepted+af+') GROUP BY reporting_scope',afa)
    result['acquisition'] = rows('''SELECT count(*) document_links_discovered,
        coalesce(sum(CAST((EXISTS(SELECT 1 FROM artifact_contents c WHERE c.artifact_id=a.artifact_id)) AS INTEGER)),0) documents_already_stored,
        coalesce(sum(CAST((EXISTS(SELECT 1 FROM artifact_download_attempts t WHERE t.artifact_id=a.artifact_id
          AND t.result_status='validated' AND t.fetched_at_utc>='2026-10-09T00:00:00+00:00')) AS INTEGER)),0) new_documents_downloaded_since_epoch,
        coalesce(sum(CAST((NOT EXISTS(SELECT 1 FROM artifact_contents c WHERE c.artifact_id=a.artifact_id)
          AND EXISTS(SELECT 1 FROM artifact_download_queue q WHERE q.artifact_id=a.artifact_id
          AND q.status IN ('PENDING','RETRYABLE_FAILURE','LEASED'))) AS INTEGER)),0) documents_awaiting_download,
        coalesce(sum(CAST((EXISTS(SELECT 1 FROM artifact_sources s JOIN credit_rating_host_policy p
          ON s.document_url LIKE 'https://'||p.host||'/%' WHERE s.artifact_id=a.artifact_id
          AND p.automation_status='BLOCKED_TERMS')) AS INTEGER)),0) documents_blocked_source_policy,
        coalesce(sum(CAST((EXISTS(SELECT 1 FROM artifact_contents c WHERE c.artifact_id=a.artifact_id
          AND c.extraction_status='ok')) AS INTEGER)),0) document_processing_completed
        FROM document_artifacts a WHERE '''+ap,aa)[0]
    result['acquisition']['documents_awaiting_publication'] = rows('''SELECT count(*) n
        FROM artifact_expectations x JOIN artifact_events e USING(event_id)
        WHERE '''+ep+''' AND x.availability_status='expected_not_yet_published'
        AND NOT EXISTS(SELECT 1 FROM document_artifacts a WHERE a.company_id=x.company_id
          AND a.artifact_type=x.artifact_type AND a.period_end=e.period_end
          AND EXISTS(SELECT 1 FROM artifact_sources s WHERE s.artifact_id=a.artifact_id
            AND s.availability_status='available'))''',ea)[0]['n']
    unassigned = rows('''SELECT
        coalesce(sum(CAST((a.artifact_type<>'credit_rating') AS INTEGER)),0) quarterly_documents,
        coalesce(sum(CAST((a.artifact_type='credit_rating') AS INTEGER)),0) rating_reports
        FROM document_artifacts a WHERE a.period_end IS NULL AND a.company_id IN
        (SELECT company_id FROM ('''+accepted+af+'))',afa)[0]
    result['acquisition']['unknown_period_documents_for_quarter_companies'] = unassigned['quarterly_documents']
    result['acquisition']['rating_reports_without_reporting_quarter'] = unassigned['rating_reports']
    clock=statement(conn,"strftime('%Y-%m-%dT%H:%M:%S+00:00','now')", "to_char(CURRENT_TIMESTAMP AT TIME ZONE 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS\"+00:00\"')")
    result['download_exclusions'] = rows(f''' SELECT q.status,q.last_result_status reason,count(DISTINCT a.artifact_id) documents,
        CASE WHEN EXISTS(SELECT 1 FROM artifact_download_host_health h WHERE h.host=q.target_host
          AND h.blocked_until_utc>{clock}) THEN
          (SELECT 'host cooldown until '||h.blocked_until_utc FROM artifact_download_host_health h WHERE h.host=q.target_host)
          WHEN q.created_at_utc<'2026-10-09T00:00:00+00:00' THEN 'pre-epoch; finite campaign required'
          WHEN q.next_attempt_at_utc>{clock} THEN 'retry not due' ELSE 'canonical policy/claim check required' END eligibility
        FROM artifact_download_queue q JOIN document_artifacts a USING(artifact_id)
        WHERE '''+ap+''' AND NOT EXISTS(SELECT 1 FROM artifact_contents c WHERE c.artifact_id=a.artifact_id)
        AND q.status<>'SUCCEEDED' GROUP BY 1,2,4''',aa)
    mapping = '''WITH mapped AS (SELECT f.filing_id,coalesce(r.company_id,s.company_id) company_id
        FROM exchange_result_filings f LEFT JOIN exchange_screener_enqueues en USING(filing_id)
        LEFT JOIN exchange_company_registry r ON r.registry_company_id=en.registry_company_id
        LEFT JOIN screener_company_collection_state s
          ON s.company_path_normalized=en.company_path_normalized AND s.reporting_scope=en.reporting_scope) '''
    result['filings'] = rows(mapping+'''SELECT f.exchange,count(*) filings,count(DISTINCT r.company_id) mapped_companies,
        sum(CAST((r.company_id IS NULL) AS INTEGER)) unresolved_filings,max(f.discovered_at_utc) last_detected
        FROM exchange_result_filings f LEFT JOIN mapped r USING(filing_id)
        WHERE '''+fp+' GROUP BY f.exchange',fpa)
    result['downloads'] = rows('''SELECT a.artifact_id,a.company_id,c.current_legal_name,a.artifact_type,a.period_end,
        a.fiscal_year,a.fiscal_quarter,a.published_at_utc,a.acquisition_status,a.preferred_content_id,
        d.content_id stored_content_id,d.extraction_status,d.content_sha256,
        CASE WHEN a.artifact_type='credit_rating' THEN coalesce(r.status,q.status) ELSE q.status END status,
        CASE WHEN a.artifact_type='credit_rating' THEN coalesce(r.last_error,q.last_error) ELSE q.last_error END last_error,
        CASE WHEN a.artifact_type='credit_rating' THEN coalesce(r.updated_at_utc,q.updated_at_utc) ELSE q.updated_at_utc END updated_at_utc
        FROM document_artifacts a JOIN companies c USING(company_id)
        LEFT JOIN artifact_contents d ON d.content_id=coalesce(a.preferred_content_id,
          (SELECT cc.content_id FROM artifact_contents cc WHERE cc.artifact_id=a.artifact_id ORDER BY cc.content_id DESC LIMIT 1))
        LEFT JOIN artifact_download_queue q ON q.download_job_id=(SELECT max(j.download_job_id)
            FROM artifact_download_queue j WHERE j.artifact_id=a.artifact_id)
        LEFT JOIN credit_rating_download_queue r ON r.download_job_id=(SELECT max(j.download_job_id)
            FROM credit_rating_download_queue j WHERE j.artifact_id=a.artifact_id)
        WHERE '''+ap+''' AND (?=0 OR a.company_id=?)
        ORDER BY a.artifact_id DESC LIMIT 40 OFFSET ?''',(*aa,company_id,company_id,max(0,min(offset,100000))))
    result['financial_jobs'] = rows('''SELECT q.queue_item_id,q.company_path_normalized,q.result_period,
        q.reporting_scope,q.status,q.last_error_json,q.updated_at_utc FROM screener_company_queue q
        WHERE '''+fq+' ORDER BY q.updated_at_utc DESC LIMIT 40',fa)
    # Aggregate each independent fact once; joining raw queue rows would multiply
    # documents and incorrectly count duplicate scope jobs as companies.
    sql = mapping.rstrip()+''', accepted AS ('''+accepted+af+'''), fin AS (
        SELECT company_id,max(CAST((reporting_scope='standalone') AS INTEGER)) standalone,
            max(CAST((reporting_scope='consolidated') AS INTEGER)) consolidated,max(accepted_at_utc) last_acceptance
        FROM accepted GROUP BY company_id), jobs AS (
        SELECT s.company_id,count(DISTINCT q.queue_item_id) admitted,
          max(CAST((q.status='accepted') AS INTEGER)) scraped,
          max(CAST((q.status IN ('failed_retryable','failed_terminal','failed','manual_review')) AS INTEGER)) failed
        FROM screener_company_queue q JOIN screener_company_collection_state s
        ON s.company_path_normalized=q.company_path_normalized AND s.reporting_scope=q.reporting_scope
        WHERE '''+fq+''' GROUP BY s.company_id), filings AS (
        SELECT r.company_id,count(DISTINCT f.filing_id) found
        FROM exchange_result_filings f JOIN mapped r USING(filing_id)
        WHERE '''+fp+''' GROUP BY r.company_id), candidates AS (
        SELECT s.company_id,count(DISTINCT x.candidate_id) found FROM screener_result_candidates x
        JOIN screener_company_collection_state s ON s.company_path_normalized=x.company_path_normalized
          AND s.reporting_scope=x.reporting_scope_hint WHERE '''+predicate('x.result_period',quarter)[0]+'''
        GROUP BY s.company_id), docs AS (
        SELECT a.company_id,count(*) documents,count(d.content_id) stored,
          sum(CAST((d.extraction_status='ok') AS INTEGER)) processed FROM document_artifacts a
        LEFT JOIN artifact_contents d ON d.content_id=coalesce(a.preferred_content_id,
          (SELECT cc.content_id FROM artifact_contents cc WHERE cc.artifact_id=a.artifact_id ORDER BY cc.content_id DESC LIMIT 1)) WHERE '''+ap+'''
        GROUP BY a.company_id), scope AS (
        SELECT company_id,max(CAST((eligibility='eligible') AS INTEGER)) eligible FROM screener_collection_scope GROUP BY company_id)
        SELECT c.company_id,c.current_legal_name,coalesce(s.eligible,0) eligible,
          coalesce(f.found,0) official_filings,coalesce(x.found,0) screener_candidates,
          coalesce(j.admitted,0) admitted_jobs,coalesce(j.scraped,0) scraped,
          coalesce(n.standalone,0) standalone,coalesce(n.consolidated,0) consolidated,
          coalesce(j.failed,0) failed,coalesce(d.documents,0) documents,
          coalesce(d.stored,0) stored,coalesce(d.processed,0) processed,n.last_acceptance,
          CASE WHEN f.found>0 THEN 'official filing detected' WHEN x.found>0 THEN 'Screener candidate detected'
            ELSE 'discovery unavailable/incomplete; publication unknown' END result_evidence
        FROM companies c LEFT JOIN scope s USING(company_id) LEFT JOIN fin n USING(company_id)
        LEFT JOIN jobs j USING(company_id) LEFT JOIN filings f USING(company_id)
        LEFT JOIN candidates x USING(company_id) LEFT JOIN docs d USING(company_id)'''
    args = (*afa,*fa,*fpa,*predicate('x.result_period',quarter)[1],*aa)
    result['coverage'] = rows('''SELECT count(*) universe,sum(eligible) eligible,
        sum(CAST((official_filings>0) AS INTEGER)) official_results_found,sum(CAST((official_filings>0 OR screener_candidates>0) AS INTEGER)) results_detected,
        sum(CAST((admitted_jobs>0) AS INTEGER)) admitted,sum(scraped) scraped,sum(standalone) standalone,
        sum(consolidated) consolidated,sum(CAST((standalone<>0 AND consolidated<>0) AS INTEGER)) both_scopes,
        sum(failed) failed,sum(CAST((documents>0) AS INTEGER)) with_documents,sum(CAST((stored>0) AS INTEGER)) with_stored_documents
        FROM ('''+sql+')',args)[0]
    contains=statement(conn,'instr(lower(current_legal_name),lower(?))','position(lower(?) in lower(current_legal_name))')
    result['companies'] = rows('SELECT * FROM ('+sql+f''' ) WHERE (?=0 OR company_id=?)
        AND (?='' OR {contains}>0 OR cast(company_id AS TEXT)=?)
        ORDER BY eligible DESC,official_filings DESC,company_id LIMIT ? OFFSET ?''',
        (*args,company_id,company_id,term,term,term,20000 if export else 100,max(0,min(offset,100000))))
    result['last_download'] = rows('''SELECT max(t.fetched_at_utc) at,count(*) validated_attempts
        FROM artifact_download_attempts t JOIN document_artifacts a USING(artifact_id)
        WHERE t.result_status='validated' AND '''+ap,aa)[0]
    result['query_seconds'] = round(time.monotonic()-started,6)
    return result
