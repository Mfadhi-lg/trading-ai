"""Finite, explicitly selected deterministic work; no scan or schema application."""
from ...db.database import connect
from ...db.documents.processing import ProcessingVersion, reconcile_processing, claim_processing
from ...db.document_work_claims import ExtractionBusy, release_extraction
from ...db.repo import utc_now
from ...pipeline.extract_document import extract_document, processing_version


def run_processing(*, db_path, work_ids, ocr_engine, output_dir, lifecycle=None,
                   now_utc=utc_now, connection_factory=connect):
    ids = tuple(dict.fromkeys(work_ids))
    if not 1 <= len(ids) <= 100 or any(type(x) is not int or x <= 0 for x in ids):
        raise ValueError('Select between 1 and 100 positive work IDs')
    actual = processing_version(ocr_engine)
    conn = connection_factory(db_path)
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
                    now_utc=now_utc, extraction_claim=claim, initialize_schema=False, connection_factory=connection_factory)
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


def run_automatic_processing(*, db_path, epoch, ocr_engine, output_dir,
                             limit=2, historical_limit=0, artifact_ids=None, lifecycle=None,
                             now_utc=utc_now, connection_factory=connect, artifact_types=('presentation','transcript','credit_rating'),focus_company_ids=()):
    """One bounded handoff using the durable inbox and existing worker/fences."""
    from ...db.documents.processing import automatic_processing_contents
    from ...operations.coordinator.exchange import ownership
    from ...db.database import check_schema_compatibility
    from ...documents.discovery.period_recovery import recover_period_batch
    from pathlib import Path
    conn = connection_factory(db_path)
    try:
        check_schema_compatibility(conn)
        with ownership(db_path, 'automatic-document-processing') as acquired:
            if not acquired:
                return {'status':'busy','selected':[],'outcomes':[]}
            version = processing_version(ocr_engine)
            selected = automatic_processing_contents(conn,version=version,
                now=now_utc(),epoch=epoch,limit=limit,historical_limit=historical_limit,artifact_ids=artifact_ids,artifact_types=artifact_types,focus_company_ids=focus_company_ids)
            def recover_periods():
                return recover_period_batch(conn,
                    state_path=Path(db_path).with_name('document-period-recovery.json'),
                    corrected_at_utc=now_utc(),
                    stopped=lifecycle.is_set if lifecycle is not None else lambda: False)
            if not selected:
                return {'status':'idle','selected':[],'outcomes':[],
                        'period_recovery':recover_periods()}
            jobs = reconcile_processing(conn,content_ids=[r['content_id'] for r,_ in selected],
                version=version,now=now_utc())
            outcomes = run_processing(db_path=db_path,work_ids=[j['work_id'] for j in jobs],
                ocr_engine=ocr_engine,output_dir=output_dir,lifecycle=lifecycle,now_utc=now_utc,connection_factory=connection_factory)
            return {'status':'recorded','version':version.values(),
                'selected':[{'content_id':r['content_id'],'priority':lane} for r,lane in selected],
                'outcomes':outcomes,'period_recovery':recover_periods()}
    finally:
        conn.close()
