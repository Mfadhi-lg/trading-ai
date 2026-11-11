"""Bounded, read-only production queries. Invoked only under host ownership gates."""
import json
import re
from pathlib import Path
import sqlite3
import sys
import time
from postgresql_runtime import query_deadline
from scrapper.db.repository_backend import is_postgresql, statement
from datetime import datetime, timezone
sys.path[:0]=['/activation','/app']
from sqlite_sidecar_guard import assert_safe_empty_wal, snapshot


def monitor_details(conn):
    """Separate stored coverage from queue work; tolerate older snapshot schemas."""
    def rows(sql):
        deadline = time.monotonic() + 4
        query_deadline(conn,deadline)
        return [dict(r) for r in conn.execute(sql)]
    result = {'detail_errors': []}
    sections = {
        'quarterly_coverage': '''SELECT f.period_end,a.reporting_scope,
            count(DISTINCT a.company_id) companies
            FROM accepted_page_versions a JOIN financial_observations f ON f.parse_id=a.parse_id
            WHERE f.period_type='quarter' AND f.section_code='quarters'
            AND f.row_position=(SELECT min(h.row_position) FROM financial_observations h
                WHERE h.parse_id=a.parse_id AND h.section_code='quarters')
            GROUP BY f.period_end,a.reporting_scope ORDER BY f.period_end DESC LIMIT 16''',
        'quarterly_queue': '''SELECT result_period,reporting_scope,status,count(*) n
            FROM screener_company_queue GROUP BY 1,2,3 ORDER BY result_period DESC LIMIT 160''',
        'collection_scope': '''SELECT eligibility state,count(*) n
            FROM screener_collection_scope GROUP BY eligibility''',
    }
    for key, sql in sections.items():
        try:
            result[key] = rows(sql)
        except sqlite3.Error as exc:
            result['detail_errors'].append(key + ': ' + str(exc))
    result['queue_diagnostics'] = []
    for name, error, pending, retry in (
        ('screener_company_queue', 'last_error_json', 'pending', 'failed_retryable'),
        ('artifact_download_queue', 'last_error', 'PENDING', 'RETRYABLE_FAILURE'),
        ('credit_rating_download_queue', 'last_error', 'PENDING', 'RETRYABLE_FAILURE'),
    ):
        try:
            deadline = time.monotonic() + 4
            query_deadline(conn,deadline)
            due=statement(conn,"julianday(next_attempt_at_utc)<=julianday('now')", "NULLIF(next_attempt_at_utc,'')::timestamptz<=CURRENT_TIMESTAMP")
            waiting=statement(conn,"julianday(next_attempt_at_utc)>julianday('now')", "NULLIF(next_attempt_at_utc,'')::timestamptz>CURRENT_TIMESTAMP")
            counts = dict(conn.execute(f'''SELECT
                sum(CAST((CASE WHEN status='{pending}' AND attempt_count=0 THEN 1 ELSE 0 END) AS INTEGER)) unattempted,
                sum(CAST((CASE WHEN status='{retry}' AND (next_attempt_at_utc IS NULL OR
                    {due}) THEN 1 ELSE 0 END) AS INTEGER)) retry_due,
                sum(CAST((CASE WHEN status='{retry}' AND {waiting}
                    THEN 1 ELSE 0 END) AS INTEGER)) retry_waiting FROM {name}''').fetchone())
            reasons = rows(f'''SELECT status,substr({error},1,500) reason,count(*) n,
                min(next_attempt_at_utc) next_attempt_at_utc FROM {name}
                WHERE status='{retry}' GROUP BY status,{error} ORDER BY n DESC LIMIT 5''')
            result['queue_diagnostics'].append(dict(queue=name, **counts, reasons=reasons))
        except sqlite3.Error as exc:
            result['detail_errors'].append(name + ': ' + str(exc))
    return result


def query(conn, action, term='', company_id=0, offset=0, quarter='all'):
    conn.row_factory = sqlite3.Row
    deadline = time.monotonic() + 4
    query_deadline(conn,deadline)
    def rows(sql, args=()):
        return [dict(r) for r in conn.execute(sql, args)]
    if action == 'pilot_validation':
        ids = tuple(int(v) for v in term.split(','))
        if not 1 <= len(ids) <= 100 or any(v < 1 for v in ids):
            raise ValueError('Select at most 100 canonical content IDs')
        return {'contents': rows("""SELECT c.content_id,c.content_sha256,c.byte_size,c.extraction_status,
            w.work_id,w.extraction_id,e.parser_name,e.parser_version,e.ocr_engine,e.ocr_engine_version,
            e.extraction_status accepted_extraction_status,
            (SELECT count(*) FROM document_pages p WHERE p.extraction_id=e.extraction_id) pages,
            (SELECT count(*) FROM document_pages p WHERE p.extraction_id=e.extraction_id AND p.content_id<>c.content_id) cross_content_pages
            FROM artifact_contents c JOIN document_processing_work w USING(content_id)
            JOIN document_extractions e ON e.extraction_id=w.extraction_id
            WHERE c.content_id IN ("""+','.join('?' for _ in ids)+')',ids)}
    if action == 'download_diagnostics':
        return {'pending_epoch':rows("""SELECT status,
            sum(CAST((created_at_utc<'2026-10-09T00:00:00+00:00') AS INTEGER)) pre_epoch,
            sum(CAST((created_at_utc>='2026-10-09T00:00:00+00:00') AS INTEGER)) post_epoch,count(*) jobs
            FROM artifact_download_queue WHERE status IN ('PENDING','RETRYABLE_FAILURE','LEASED') GROUP BY status"""),
            'validated_downloads_since_baseline':rows("""SELECT count(*) attempts,count(DISTINCT content_id) contents,
                coalesce(CAST(sum(byte_size) AS BIGINT),0) bytes FROM artifact_download_attempts
                WHERE result_status='validated' AND fetched_at_utc>='2026-10-30T04:52:16+00:00'"""),
            'host_policies':rows('SELECT * FROM credit_rating_host_policy ORDER BY host LIMIT 30')}
    if action == 'phase3_audit':
        return {'enqueues': rows('SELECT e.*,r.company_id FROM exchange_screener_enqueues e LEFT JOIN exchange_company_registry r USING(registry_company_id) ORDER BY enqueue_id DESC LIMIT 5'),
            'states': rows('SELECT * FROM screener_company_collection_state LIMIT 5'),
            'schemas': rows("SELECT name,sql FROM sqlite_master WHERE type='table' AND name IN ('companies','source_identifiers','artifact_contents','artifact_download_host_policies')"),
            'scope': rows('SELECT company_path,company_id FROM screener_collection_scope WHERE company_id IS NOT NULL LIMIT 5')}
    if action == 'quarter_options':
        from acquisition_quarter_queries import options
        return {'options': options(conn)}
    if action == 'quarter_coverage_export':
        from acquisition_quarter_queries import monitor
        return monitor(conn,quarter,export=True)
    if action == 'quarter_monitor':
        from acquisition_quarter_queries import monitor
        return monitor(conn, quarter, term=term, offset=offset, company_id=company_id)
    if action == 'company_catalog':
        identifiers=statement(conn,"group_concat(identifier_value,' ')","string_agg(identifier_value,' ')")
        # One guarded read supplies in-memory completion; typing never queries SQLite.
        return {'companies': rows(f'''WITH symbols AS (
            SELECT company_id,min(source_symbol) symbol FROM source_identifiers
            WHERE source='screener' GROUP BY company_id), ids AS (
            SELECT company_id,{identifiers} identifiers
            FROM security_identifiers WHERE is_current=1 GROUP BY company_id)
            SELECT c.company_id,c.current_legal_name,s.symbol,i.identifiers
            FROM companies c LEFT JOIN symbols s ON s.company_id=c.company_id
            LEFT JOIN ids i ON i.company_id=c.company_id
            ORDER BY c.current_legal_name LIMIT 20001''')}
    if action == 'company_report':
        matches = query(conn, 'search', term=term)['companies']
        exact = [r for r in matches if str(r['company_id']) == term.strip()
                 or (r.get('symbol') or '').lower() == term.strip().lower()
                 or r['current_legal_name'].lower() == term.strip().lower()]
        if not matches or (not exact and len(matches) > 1):
            return {'matches': matches, 'company': None}
        selected = (exact or matches)[0]
        result = query(conn, 'company', company_id=selected['company_id'])
        try:
            result['financials'] = query(conn, 'financials', company_id=selected['company_id'])['text']
        except (ValueError, RuntimeError, sqlite3.Error) as exc:
            result['financials_error'] = str(exc)
        if quarter != 'all':
            from acquisition_quarter_queries import monitor
            result['quarter_acquisition'] = monitor(conn,quarter,company_id=selected['company_id'])
        return result
    if action == 'summary':
        result = {
            'companies': conn.execute('SELECT count(*) FROM companies').fetchone()[0],
            'accepted_pages': conn.execute('SELECT count(*) FROM accepted_page_versions').fetchone()[0],
            'document_contents': rows('SELECT extraction_status state,count(*) n FROM artifact_contents GROUP BY 1'),
            'documents': rows('SELECT acquisition_status state,count(*) n FROM document_artifacts GROUP BY 1'),
            'discovery': rows('SELECT availability_status state,count(*) n FROM artifact_expectations GROUP BY 1'),
            'financial_outcomes': rows("SELECT status,last_error_json,count(*) n FROM screener_company_queue WHERE status IN ('failed','failed_retryable','failed_terminal','manual_review') GROUP BY status,last_error_json"),
            'active_financial_jobs': rows("SELECT queue_item_id,company_path_normalized,result_period,reporting_scope,status,attempt_count,claimed_at_utc,updated_at_utc,last_error_json FROM screener_company_queue WHERE status IN ('claimed','running') ORDER BY updated_at_utc DESC LIMIT 8"),
            'recent_financial_jobs': rows('SELECT queue_item_id,company_path_normalized,reporting_scope,result_period,status,attempt_count,created_at_utc,updated_at_utc,last_error_json FROM screener_company_queue ORDER BY updated_at_utc DESC LIMIT 30')}
        result.update(monitor_details(conn))
        return result
    if action == 'financials':
        # A separate root-level mount avoids trying to create a file inside the
        # already read-only /host directory mount.
        if Path('/activation').exists():
            sys.path.insert(0, '/')
        try:
            # The read-only census image runs scripts outside the application
            # package. Mount the existing renderer explicitly; no image rebuild.
            from acquisition_financial_renderer import render_company, resolve_company
        except ModuleNotFoundError as exc:
            if exc.name != 'acquisition_financial_renderer' or Path('/activation').exists():
                raise
            from scrapper.view.render import render_company, resolve_company
        company = conn.execute('SELECT current_legal_name FROM companies WHERE company_id=?', (company_id,)).fetchone()
        if not company:
            return {'text': 'Company not found.'}
        term = company['current_legal_name']
        resolved = resolve_company(conn, term)
        if not resolved or resolved['company_id'] != company_id:
            raise ValueError('Company name is ambiguous; financial matrix identity could not be verified')
        return {'text': render_company(conn, term, periods=6)}
    if action == 'search':
        # Normalize display-name punctuation; also search current exchange and
        # Screener identifiers. UNION avoids duplicate rows and scans each
        # identifier table once, rather than once per company.
        term = term.strip()[:100]
        normalized = re.sub(r'[^a-z0-9]', '', term.lower())
        name = 'lower(c.current_legal_name)'
        for char in (' ', '.', '-', '&', '(', ')', "'"):
            name = "replace(" + name + ", '" + char.replace("'", "''") + "', '')"
        search_sql='''
            SELECT c.company_id,c.current_legal_name,c.status,
              (SELECT s.source_symbol FROM source_identifiers s
               WHERE s.company_id=c.company_id AND s.source='screener' LIMIT 1) AS symbol
            FROM companies c WHERE c.company_id IN (
            SELECT c.company_id FROM companies c
            WHERE ?='' OR instr(lower(c.current_legal_name),lower(?))>0
              OR (?<>'' AND instr(''' + name + ''',?)>0)
              OR CAST(c.company_id AS TEXT)=?
            UNION SELECT i.company_id FROM security_identifiers i
                 WHERE i.is_current=1 AND instr(lower(i.identifier_value),lower(?))>0
            UNION SELECT s.company_id FROM source_identifiers s WHERE
                   (instr(lower(COALESCE(s.source_symbol,'')),lower(?))>0
                    OR instr(lower(COALESCE(s.source_slug,'')),lower(?))>0))
            ORDER BY CASE WHEN c.company_id IN (SELECT i.company_id FROM security_identifiers i
                 WHERE i.is_current=1 AND lower(i.identifier_value)=lower(?)) THEN 0
                 WHEN lower(c.current_legal_name)=lower(?) THEN 1 ELSE 2 END,
                 c.current_legal_name,c.company_id LIMIT 41 OFFSET ?'''
        if is_postgresql(conn):
            search_sql='''
            SELECT c.company_id,c.current_legal_name,c.status,
              (SELECT s.source_symbol FROM source_identifiers s
               WHERE s.company_id=c.company_id AND s.source='screener' LIMIT 1) AS symbol
            FROM companies c WHERE c.company_id IN (
            SELECT c.company_id FROM companies c
            WHERE ?='' OR position(lower(?) in lower(c.current_legal_name))>0
              OR (?<>'' AND position(? in ''' + name + ''')>0)
              OR CAST(c.company_id AS TEXT)=?
            UNION SELECT i.company_id FROM security_identifiers i
                 WHERE i.is_current=1 AND position(lower(?) in lower(i.identifier_value))>0
            UNION SELECT s.company_id FROM source_identifiers s WHERE
                   (position(lower(?) in lower(COALESCE(s.source_symbol,'')))>0
                    OR position(lower(?) in lower(COALESCE(s.source_slug,'')))>0))
            ORDER BY CASE WHEN c.company_id IN (SELECT i.company_id FROM security_identifiers i
                 WHERE i.is_current=1 AND lower(i.identifier_value)=lower(?)) THEN 0
                 WHEN lower(c.current_legal_name)=lower(?) THEN 1 ELSE 2 END,
                 c.current_legal_name,c.company_id LIMIT 41 OFFSET ?'''
        return {'companies': rows(search_sql,
            (term,term,normalized,normalized,term,term,term,term,term,term,max(0,min(offset,100000))))}
    if action != 'company':
        raise ValueError('Unsupported read-only query')
    company = rows('SELECT * FROM companies WHERE company_id=?', (company_id,))
    if not company:
        return {'company': None}
    age_seconds=statement(conn,"CAST((julianday('now')-julianday(q.created_at_utc))*86400 AS INTEGER)",
        "trunc(extract(epoch FROM CURRENT_TIMESTAMP-NULLIF(q.created_at_utc,'')::timestamptz))::bigint")
    accepted = 'SELECT parse_id FROM accepted_page_versions WHERE company_id=?'
    result = {'company': company[0], 'limit_per_section': 100,
        'identifiers': rows('SELECT identifier_type,identifier_value FROM security_identifiers WHERE company_id=? AND is_current=1 LIMIT 100',(company_id,)),
        'accepted_pages': rows('SELECT source,reporting_scope,resolved_source_period,accepted_at_utc,parse_id FROM accepted_page_versions WHERE company_id=? LIMIT 100',(company_id,)),
        'periods': rows('SELECT DISTINCT reporting_scope,section_code,period_end,period_type,period_label_raw FROM financial_observations WHERE parse_id IN ('+accepted+') ORDER BY period_end DESC LIMIT 100',(company_id,)),
        'financial_jobs': rows(f"SELECT q.queue_item_id,q.company_path_normalized,q.result_period,q.reporting_scope,q.status,{age_seconds} queue_age_seconds,q.created_at_utc,q.updated_at_utc,q.last_error_json,q.accepted_export_path FROM screener_company_queue q WHERE q.company_path_normalized IN (SELECT company_path_normalized FROM screener_company_collection_state WHERE company_id=?) ORDER BY q.updated_at_utc DESC LIMIT 100",(company_id,)),
        'filings': rows('SELECT filing_id,exchange,company_name_raw,result_period,reporting_scope,broadcast_at_utc,discovered_at_utc FROM exchange_result_filings WHERE isin IN (SELECT identifier_value FROM security_identifiers WHERE company_id=? AND identifier_type=\'isin\') OR exchange_key IN (SELECT identifier_value FROM security_identifiers WHERE company_id=?) ORDER BY discovered_at_utc DESC LIMIT 100',(company_id,company_id)),
        'documents': rows('SELECT a.artifact_id,a.artifact_type,a.title_raw,a.period_end,a.event_date,a.acquisition_status,a.first_observed_at_utc,c.content_sha256,c.extraction_status,b.local_path,c.created_at_utc FROM document_artifacts a LEFT JOIN artifact_contents c ON c.content_id=a.preferred_content_id LEFT JOIN response_blobs b ON b.blob_id=c.blob_id WHERE a.company_id=? ORDER BY a.first_observed_at_utc DESC LIMIT 100',(company_id,)),
        'expectations': rows('SELECT artifact_type,availability_status,terminal_reason,last_error_json,last_checked_at_utc FROM artifact_expectations WHERE company_id=? LIMIT 100',(company_id,)),
        'links': rows('SELECT document_type,title_raw,period_label_raw,availability_status,source_url,first_observed_at_utc FROM document_links WHERE parse_id IN ('+accepted+') LIMIT 100',(company_id,))}
    result['absence_meaning'] = 'No record means not discovered / unknown, unless an explicit availability record confirms otherwise.'
    result['missing_periods'] = 'Unknown: no authoritative expected-period denominator. Pending/failed result periods are listed in financial_jobs.'
    result['verification_meaning'] = 'Persisted acquisition/validation state and stored hashes; files are not rehashed by monitoring.'
    return result


def inspect(db, action, *, live=False, **kwargs):
    from postgresql_runtime import enabled,read_connection
    native=enabled()
    before = None if live or native else assert_safe_empty_wal(db)
    conn = read_connection(db,immutable=not live)
    try:
        if not native:
            conn.execute('PRAGMA temp_store=MEMORY')
            if live:conn.execute('BEGIN')
        if not live:
            from production_ownership import assert_no_conflicting_leases
            assert_no_conflicting_leases(conn, now=datetime.now(timezone.utc).isoformat())
        result = query(conn, action, **kwargs)
    finally:
        conn.close()
    if not live and not native and snapshot(db) != before['files']:
        raise RuntimeError('Database changed during inspection; result discarded')
    return result


if __name__ == '__main__':
    try:
        print(json.dumps(inspect(Path('/data/scraper.db'),sys.argv[1],**json.loads(sys.argv[2]))))
    except Exception as exc:
        raise SystemExit(type(exc).__name__+': '+str(exc))
