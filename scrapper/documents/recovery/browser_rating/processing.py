"""Existing run-scoped extraction/normalization and immutable PDF repair."""
import json
from ....db.document_work_claims import ExtractionBusy, OwnershipLost
from ....db.documents.recovery import browser_rating_content as _repository
from ....db.repo import utc_now
from ....db.html_document_rendering_repo import render_and_persist_html_pdf
from ...html_pdf import HTML_PDF_RENDERER_VERSION
from ...store import assert_document_store_ready

def process_reports(conn, args, owner, *, STOP, worker_state, emit, utc_now=utc_now):
    """Extract and normalize this run's imported reports, with durable outcomes."""
    from ....pipeline.extract_document import extract_document
    from ....pipeline.normalize_credit_rating import normalize_credit_rating_document
    processed = 0
    while not STOP.is_set() and (not args.limit or processed < args.limit):
        worker_state(conn, args.run_id, owner, 'EXTRACTING')
        job = _repository.next_processing_job(conn, args.run_id)
        if job is None:
            pending = _repository.pending_count(conn, args.run_id)
            if args.follow and pending:
                worker_state(conn, args.run_id, owner, 'WAITING')
                STOP.wait(10)
                continue
            break
        evidence = json.loads(job['evidence_json'])
        try:
            original = extract_document(artifact_id=job['artifact_id'], content_id=job['content_id'], db_path=args.db)
            evidence['original_extraction_id'] = original['extraction']['extraction_id']
            pdf_id = evidence.get('rendered_content_id', job['content_id'])
            pdf = original if pdf_id == job['content_id'] else extract_document(
                artifact_id=job['artifact_id'], content_id=pdf_id, db_path=args.db)
            evidence['pdf_extraction_id'] = pdf['extraction']['extraction_id']
            if pdf['extraction']['extraction_status'] != 'ok':
                raise ValueError('PDF extraction is not usable for rating normalization')
            normalized = normalize_credit_rating_document(artifact_id=job['artifact_id'],
                extraction_id=pdf['extraction']['extraction_id'], db_path=args.db)
            evidence['normalization_id'] = normalized['normalization']['normalization_id']
            evidence['normalization_status'] = normalized['normalization']['normalization_status']
            evidence['processing_status'] = 'DONE'
        except (ExtractionBusy, OwnershipLost):
            worker_state(conn, args.run_id, owner, 'WAITING')
            if args.follow:
                STOP.wait(10)
                continue
            break
        except Exception as error:
            evidence['processing_status'] = 'FAILED'
            evidence['processing_error'] = f'{type(error).__name__}: {error}'[:3000]
        _repository.save_processing_evidence(conn, job['browser_job_id'],
            json.dumps(evidence, sort_keys=True), utc_now())
        emit({'artifact_id': job['artifact_id'], 'processing_status': evidence['processing_status'],
              'normalization_id': evidence.get('normalization_id'), 'error': evidence.get('processing_error')})
        processed += 1


def repair_pdf_derivatives(conn, args, *, emit, utc_now=utc_now,
                           assert_document_store_ready=assert_document_store_ready,
                           render_and_persist_html_pdf=render_and_persist_html_pdf,
                           HTML_PDF_RENDERER_VERSION=HTML_PDF_RENDERER_VERSION):
    """Version a corrected PDF copy; original HTML and old versions are immutable."""
    jobs = _repository.html_jobs(conn, args.run_id)
    count = 0
    for job in jobs:
        if args.limit and count >= args.limit:
            break
        evidence = json.loads(job['evidence_json'])
        if evidence.get('renderer_version') == HTML_PDF_RENDERER_VERSION:
            continue
        assert_document_store_ready()
        rendering = render_and_persist_html_pdf(conn, artifact_id=job['artifact_id'],
            source_content_id=job['content_id'], source_url=job['final_url'] or job['requested_url'],
            allow_browser_snapshot=job['content_origin'] == 'generated_derivative')
        previous = evidence.get('rendered_content_id')
        if previous and previous != rendering.rendered_content_id:
            evidence.setdefault('previous_rendered_content_ids', []).append(previous)
            if evidence.get('normalization_id'):
                evidence.setdefault('previous_normalization_ids', []).append(evidence['normalization_id'])
            evidence.pop('processing_status', None)
            evidence.pop('processing_error', None)
        evidence['rendered_content_id'] = rendering.rendered_content_id
        evidence['renderer_version'] = HTML_PDF_RENDERER_VERSION
        _repository.save_processing_evidence(conn, job['browser_job_id'],
            json.dumps(evidence, sort_keys=True), utc_now())
        count += 1
    emit({'run_id': args.run_id, 'corrected_pdf_derivatives': count})


