"""Finite, explicitly selected deterministic work; no scan or schema application."""
from ...db.database import connect
from ...db.documents.processing import ProcessingVersion, reconcile_processing, claim_processing
from ...db.document_work_claims import ExtractionBusy, release_extraction
from ...db.repo import utc_now
from ...pipeline.extract_document import extract_document, processing_version


def run_processing(*, db_path, work_ids, ocr_engine, output_dir, lifecycle=None,
                   now_utc=utc_now):
    ids = tuple(dict.fromkeys(work_ids))
    if not 1 <= len(ids) <= 100 or any(type(x) is not int or x <= 0 for x in ids):
        raise ValueError('Select between 1 and 100 positive work IDs')
    actual = processing_version(ocr_engine)
    conn = connect(db_path)
    results = []
    try:
        # Preflight the whole selection before writing or taking ownership.
        jobs = []
        for work_id in ids:
            row = conn.execute("""SELECT w.*, c.artifact_id FROM document_processing_work w
                JOIN artifact_contents c USING(content_id) WHERE work_id=?""", (work_id,)).fetchone()
            if row is None:
                raise ValueError('Unknown processing work')
            version = ProcessingVersion(*(row[k] for k in
                ('parser_name','parser_version','ocr_engine','ocr_engine_version','options_json')))
            if version != actual:
                raise ValueError('Worker parser/OCR/options do not match selected work')
            jobs.append(dict(row))
        for job in jobs:
            if lifecycle is not None and lifecycle.is_set():
                break
            recovered = reconcile_processing(conn, content_ids=[job['content_id']],
                version=actual, now=now_utc())[0]
            claim = None
            if recovered['extraction_id'] is None:
                try:
                    claim = claim_processing(conn, work_id=job['work_id'])
                except ExtractionBusy:
                    results.append({'work_id':job['work_id'], 'state':'busy'})
                    continue
            if lifecycle is not None:
                lifecycle.claimed(job['work_id'])
            try:
                payload = extract_document(artifact_id=job['artifact_id'],
                    content_id=job['content_id'], db_path=db_path, ocr_engine=ocr_engine,
                    output=output_dir / ('work-%s.json' % job['work_id']),
                    now_utc=now_utc, extraction_claim=claim, initialize_schema=False)
                completed = reconcile_processing(conn, content_ids=[job['content_id']],
                    version=actual, now=now_utc())[0]
                results.append({'work_id':job['work_id'], 'state':'recorded',
                    'extraction_id':completed['extraction_id'],
                    'extraction_status':payload['extraction']['extraction_status']})
            finally:
                conn.rollback()
                if claim is not None:
                    with conn:
                        release_extraction(conn, claim)
                if lifecycle is not None:
                    lifecycle.finished()
        return results
    finally:
        conn.close()
