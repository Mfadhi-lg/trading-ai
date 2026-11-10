"""Document metadata refresh with no financial observation or acceptance writes."""
from ...db.database import connect,check_schema_compatibility
from ...db.repo import store_blob,record_fetch,start_run,finish_run,get_or_create_parse_artifact,utc_now
from ...sources.screener.company import parse_company_page
from ...pipeline.accepted_document_downloads import enqueue_concall_documents_for_accepted_parse
from ...config import MANIFEST_VERSION
from ...db.repository_backend import statement


def ingest_document_response(*, response, company_id, result_period, db_path, snapshot_root,
                             ownership_check=None, connection_factory=connect):
    if company_id is None or result_period is None or response.fetch_status!='ok':
        raise ValueError('Document refresh requires successful exact-company source evidence')
    parsed=parse_company_page(response.content,utc_now())
    if parsed.reporting_scope!='standalone': raise ValueError('Unexpected reporting scope')
    conn=connection_factory(db_path)
    try:
        check_schema_compatibility(conn)
        mapping=conn.execute("SELECT source_company_id FROM source_identifiers WHERE company_id=? AND source='screener' AND mapping_status='verified'",(company_id,)).fetchone()
        identities={(r[0],r[1]) for r in conn.execute('SELECT identifier_type,identifier_value FROM security_identifiers WHERE company_id=? AND is_current=1',(company_id,))}
        if mapping:
            if str(mapping[0])!=str(parsed.source_company_id): raise ValueError('Document refresh changed source company identity')
        elif not (('NSE',parsed.nse_symbol) in identities or ('BSE',parsed.bse_code) in identities):
            raise ValueError('Document refresh lacks verified company identity')
        if ownership_check: ownership_check(conn)
        run=start_run(conn,'document-only-links-1',{'company_id':company_id,'document_only':True})
        try:
            now=utc_now()
            blob=store_blob(conn,response,f'company-{company_id}','standalone',now,snapshot_root)
            record_fetch(conn,run,company_id,response,'standalone',now,blob)
            parse,status=get_or_create_parse_artifact(conn,blob,'document-only-links','1.0.1',MANIFEST_VERSION)
            with conn:
                conn.executemany(statement(conn, '''INSERT OR IGNORE INTO document_links
                    (parse_id,reporting_scope,document_type,period_label_raw,title_raw,position,source_url,publisher,
                     availability_status,first_observed_at_utc,available_at_utc,availability_basis)
                    VALUES (?,'standalone',?,?,?,?,?,?,?,?,?,'first_observed')''', "INSERT INTO document_links\n                    (parse_id,reporting_scope,document_type,period_label_raw,title_raw,position,source_url,publisher,\n                     availability_status,first_observed_at_utc,available_at_utc,availability_basis)\n                    VALUES (?,'standalone',?,?,?,?,?,?,?,?,?,'first_observed') ON CONFLICT DO NOTHING"),
                    [(parse,l.document_type,l.period_label_raw,l.title_raw,l.position,l.source_url,l.publisher,l.availability_status,now,now) for l in parsed.document_links])
                conn.execute("UPDATE parse_artifacts SET parse_status='ok',validation_status='passed' WHERE parse_id=?",(parse,))
            enqueue_concall_documents_for_accepted_parse(conn,parse_id=parse,verified_company_id=company_id,
                resolved_result_period=result_period)
            finish_run(conn,run,'ok')
            from ..screener.ingest import DocumentRefreshResult
            return DocumentRefreshResult(parse,company_id,status=='ok')
        except BaseException:
            conn.rollback();finish_run(conn,run,'error');raise
    finally: conn.close()
