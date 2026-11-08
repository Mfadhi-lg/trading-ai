"""Reparse bounded saved, accepted pages without inventing a new fetch."""
import hashlib
from pathlib import Path
from bs4 import BeautifulSoup
from ...config import MANIFEST_VERSION
from ...db.repo import get_or_create_parse_artifact
from ...documents.discovery.promotion import call_matches_result_window
from ...pipeline.accepted_document_downloads import enqueue_concall_documents_for_accepted_parse
from ...sources.screener.document_links import _parse_documents


def refresh_saved_links(conn, *, company_id, accepted_parse_id, period_end):
    row=conn.execute('''SELECT b.* FROM accepted_page_versions p JOIN parse_artifacts pa USING(parse_id)
        JOIN response_blobs b USING(blob_id) WHERE p.company_id=? AND p.parse_id=? AND p.source='screener' AND p.reporting_scope='standalone'
        AND EXISTS(SELECT 1 FROM financial_observations f WHERE f.parse_id=p.parse_id
         AND f.section_code='quarters' AND f.period_end=?)''',
        (company_id,accepted_parse_id,period_end)).fetchone()
    if row is None:raise ValueError('Saved link refresh requires accepted company/quarter evidence')
    # Promotion independently verifies the original company fetch for this blob.
    body=Path(row['local_path']).read_bytes()
    if hashlib.sha256(body).hexdigest()!=row['content_sha256']:raise ValueError('Saved Screener page hash mismatch')
    _, _, links=_parse_documents(BeautifulSoup(body,'html.parser'))
    selected=[l for l in links if l.document_type in ('concall_ppt','concall_transcript','concall_recording')
              and call_matches_result_window(l.period_label_raw,period_end)]
    if len(selected)>12:raise ValueError('Saved page current-call link bound exceeded')
    parse,status=get_or_create_parse_artifact(conn,row['blob_id'],
        'saved-document-links-'+period_end,'1.0.1',MANIFEST_VERSION)
    with conn:
        if status=='pending':
            conn.executemany('''INSERT INTO document_links
                (parse_id,reporting_scope,document_type,period_label_raw,title_raw,position,source_url,publisher,
                availability_status,first_observed_at_utc,available_at_utc,availability_basis)
                VALUES (?,'standalone',?,?,?,?,?,?,?,?,?,'first_observed')''',
                [(parse,l.document_type,l.period_label_raw,l.title_raw,l.position,l.source_url,l.publisher,
                  l.availability_status,row['first_observed_at_utc'],row['first_observed_at_utc']) for l in selected])
            conn.execute("UPDATE parse_artifacts SET parse_status='ok',validation_status='passed' WHERE parse_id=?",(parse,))
        elif status!='ok':raise ValueError('Saved link parser previously failed')
    result=enqueue_concall_documents_for_accepted_parse(conn,parse_id=parse,verified_company_id=company_id,
        resolved_result_period=period_end[:7],include_queue_counts=False)
    return {'company_id':company_id,'original_parse_id':accepted_parse_id,'parse_id':parse,
            'blob_id':row['blob_id'],'sha256':row['content_sha256'],'link_count':len(selected),'handoff':result}
