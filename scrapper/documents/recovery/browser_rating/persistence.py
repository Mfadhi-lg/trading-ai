"""Persist browser recovery through canonical content/download owners."""
from datetime import datetime, timedelta
from ....db.documents.recovery import browser_rating_content as _repository
from ....db.document_download_repo import persist_download_result, store_response_blob
from ....db.html_document_rendering_repo import render_and_persist_html_pdf
from ....db.repo import utc_now
from ...downloader import DownloadPolicy
from ...html_pdf import HTML_PDF_RENDERER_VERSION
from ...store import assert_document_store_ready

def import_report(conn, job, result, evidence, run_id, *, assert_document_store_ready=assert_document_store_ready,
                  utc_now=utc_now, store_response_blob=store_response_blob,
                  persist_download_result=persist_download_result, DownloadPolicy=DownloadPolicy,
                  datetime=datetime, render_and_persist_html_pdf=render_and_persist_html_pdf,
                  HTML_PDF_RENDERER_VERSION=HTML_PDF_RENDERER_VERSION):
    assert_document_store_ready()
    existing = _repository.existing_content(conn, job['artifact_id'])
    if existing:
        return 'SKIPPED_EXISTING', int(existing['content_id']), evidence
    ids = _repository.source_ids(conn, job['artifact_id'], job['requested_url'])
    now = utc_now()
    browser_snapshot = evidence.get('html_capture') == 'browser_rendered_dom_snapshot'
    if browser_snapshot:
        with conn:
            blob_id, digest = store_response_blob(conn, body=evidence.pop('_original_http_body'),
                media_type='text/html', observed_at_utc=now)
        evidence.update({'original_http_blob_id': blob_id, 'original_http_sha256': digest})
    saved = persist_download_result(conn, artifact_id=job['artifact_id'], artifact_source_ids=ids,
        result=result, policy=DownloadPolicy(),
        started_at_utc=(datetime.fromisoformat(now) - timedelta(milliseconds=result.elapsed_ms)).isoformat(),
        fetched_at_utc=now,
        content_origin='generated_derivative' if browser_snapshot else 'publisher_original',
        official_override=False if browser_snapshot else None)
    content_id = saved.content_id
    evidence.update({'download_attempt_id': saved.download_attempt_id, 'content_sha256': saved.content_sha256,
                     'byte_size': saved.byte_size, 'run_id': run_id})
    if result.detected_media_type == 'text/html':
        try:
            rendered = render_and_persist_html_pdf(conn, artifact_id=job['artifact_id'],
                source_content_id=content_id, source_url=result.final_url or job['requested_url'],
                allow_browser_snapshot=browser_snapshot)
            evidence['rendered_content_id'] = rendered.rendered_content_id
            evidence['renderer_version'] = HTML_PDF_RENDERER_VERSION
        except Exception as error:
            # The original remains usable even if derivative generation fails.
            evidence['pdf_derivative_error'] = f'{type(error).__name__}: {error}'
    _repository.reconcile_download(conn, job['artifact_id'], now,
        job=job if 'browser_job_id' in job.keys() else None)
    return 'SUCCEEDED', content_id, evidence


