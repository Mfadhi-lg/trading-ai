"""Incomplete report bytes remain attempt evidence, never accepted content."""
from unittest.mock import patch

import pytest

from scrapper.db.document_download_queue_repo import materialize_download_queue
from scrapper.documents.credit_rating_resolver import resolve_credit_rating
from scrapper.documents.download_scope import register_download_scope
from scrapper.documents.downloader import DownloadResult
from scrapper.pipeline.run_document_download_worker import run_document_download_worker
from scrapper.tests import test_document_download_queue as fixtures


URL = 'https://www.indiaratings.co.in/pressrelease/123'
HTML = ('<html><body><h1>Example rating rationale</h1><p>' +
        'Credit rating action assigns a stable outlook to the bank loan facility. '
        'The rating rationale discusses liquidity and rating sensitivity. ' * 20).encode()


def incomplete_result(status):
    return DownloadResult(
        requested_url=URL, final_url=URL, http_status=200,
        response_headers={'content-type': 'text/html'}, redirects=(),
        declared_media_type='text/html', detected_media_type='text/html',
        body=HTML, byte_size=len(HTML), result_status=status, elapsed_ms=100,
        error_code='total_timeout_exceeded' if status == 'timed_out' else 'stream_size_exceeded',
        error_message='Synthetic incomplete response',
    )


@pytest.mark.parametrize('status', ['timed_out', 'too_large'])
def test_resolver_does_not_accept_or_follow_partial_report_html(status):
    result = incomplete_result(status)
    with patch('scrapper.documents.credit_rating_resolver.download_url', return_value=result) as download:
        resolved = resolve_credit_rating(URL, session=object(), browser_fallback=False)
    assert resolved.final_result is None
    assert resolved.steps[0].result.result_status == status
    assert download.call_count == 1


@pytest.mark.parametrize('status', ['timed_out', 'too_large'])
def test_worker_preserves_partial_html_as_attempt_without_content_or_pdf(status):
    fixture = fixtures.DocumentDownloadQueueDA05Tests()
    fixture.setUp()
    try:
        fixture.replace_credit_rating_url(URL)
        artifact_id = fixture.conn.execute(
            "SELECT artifact_id FROM document_artifacts WHERE artifact_type='credit_rating' LIMIT 1"
        ).fetchone()[0]
        register_download_scope(
            fixture.conn, scope_id='partial-html', scope_kind='P1', companies=('LAURUSLABS',),
            artifact_types=('credit_rating',), artifact_ids=(artifact_id,),
            enforce_pilot_composition=False, created_at_utc=fixtures.NOW,
        )
        materialize_download_queue(fixture.conn, scope_id='partial-html', created_at_utc=fixtures.NOW)
        with patch('scrapper.pipeline.run_document_download_worker.download_url', return_value=incomplete_result(status)):
            outcome = run_document_download_worker(
                fixture.conn, scope_id='partial-html', limit=1, throttle_seconds=0,
                snapshot_root=fixture.root / 'content', document_store=fixture.root,
                session_factory=lambda: object(), now=lambda: fixtures.NOW,
                health_check=fixtures.FakeHealth, worker='partial-html-test',
            )
        assert outcome['outcome_counts'] == {status: 1}
        attempt = fixture.conn.execute(
            'SELECT result_status,blob_id,content_id FROM artifact_download_attempts'
        ).fetchone()
        assert attempt['result_status'] == status
        assert attempt['blob_id'] is not None
        assert attempt['content_id'] is None
        assert fixture.conn.execute('SELECT count(*) FROM html_document_renderings').fetchone()[0] == 0
        assert fixture.conn.execute(
            "SELECT status FROM artifact_download_queue WHERE scope_id='partial-html'"
        ).fetchone()[0] != 'SUCCEEDED'
    finally:
        fixture.tearDown()
