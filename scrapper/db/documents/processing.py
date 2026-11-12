"""Explicit bounded deterministic extraction handoff; no worker or corpus scan.

The inbox records all saved origins atomically. Unsupported media remain evidence,
not extraction jobs. Explicit recovery and bounded automatic selection share the
exact parser/OCR/options contract; failed cache entries remain failed.
"""
from dataclasses import dataclass
from ..document_work_claims import claim_extraction, ExtractionBusy
from ...documents.extraction import canonical_json
from ..repository_backend import is_postgresql
from scrapper.db.repository_backend import (execute, executemany, generated_id, statement, is_postgresql, atomic_repository, begin_immediate, commit, rollback, transaction as repository_transaction)


@dataclass(frozen=True)
class ProcessingVersion:
    parser_name: str
    parser_version: str
    ocr_engine: str
    ocr_engine_version: str
    options_json: str

    @classmethod
    def create(cls, *, parser_name, parser_version, ocr_engine, ocr_engine_version, options):
        if not all(isinstance(x, str) and x.strip() for x in
                   (parser_name, parser_version, ocr_engine, ocr_engine_version)):
            raise ValueError('An explicit parser and OCR version is required')
        if not isinstance(options, dict):
            raise ValueError('Explicit options are required')
        return cls(parser_name, parser_version, ocr_engine, ocr_engine_version,
                   canonical_json(options))

    def values(self):
        return (self.parser_name, self.parser_version, self.ocr_engine,
                self.ocr_engine_version, self.options_json)

    def key(self, content_id):
        return canonical_json([content_id, *self.values()])


@atomic_repository('documents')
def reconcile_processing(conn, *, content_ids, version, now):
    """Recover missed handoffs and post-persistence crashes for <=100 selected IDs.

    Requires explicitly applied 072. One short transaction; no filesystem, OCR,
    network, migration, default version or automatic re-extraction.
    """
    if is_postgresql(conn):
        from ..postgresql_workers import reconcile_processing as native
        return native(conn, content_ids=content_ids, version=version, now=now)
    ids = tuple(dict.fromkeys(content_ids))
    if not 1 <= len(ids) <= 100 or any(type(x) is not int or x <= 0 for x in ids):
        raise ValueError('Select between 1 and 100 positive content IDs')
    if conn.in_transaction:
        raise ValueError('Reconciliation needs an idle connection')
    begin_immediate(conn, lock='documents')
    result = []
    try:
        for content_id in ids:
            content = execute(conn, '''SELECT c.*, a.artifact_type FROM artifact_contents c
                JOIN document_artifacts a USING(artifact_id) WHERE c.content_id=?''',
                (content_id,)).fetchone()
            if content is None:
                raise ValueError(f'Unknown content {content_id}')
            execute(conn, statement(conn, 'INSERT OR IGNORE INTO document_processing_inbox VALUES (?,?)', 'INSERT INTO document_processing_inbox VALUES (?,?) ON CONFLICT DO NOTHING'),
                         (content_id, content['created_at_utc']))
            supported = content['media_type'] in (
                'application/pdf', 'application/vnd.ms-powerpoint',
                'application/vnd.openxmlformats-officedocument.presentationml.presentation') or (
                content['media_type'] == 'text/html' and content['artifact_type'] == 'credit_rating')
            if not supported:
                continue
            key = version.key(content_id)
            execute(conn, statement(conn, '''INSERT OR IGNORE INTO document_processing_work
                (content_id,parser_name,parser_version,ocr_engine,ocr_engine_version,
                 options_json,resource_key,created_at_utc) VALUES (?,?,?,?,?,?,?,?)''', """INSERT INTO document_processing_work
                (content_id,parser_name,parser_version,ocr_engine,ocr_engine_version,
                 options_json,resource_key,created_at_utc) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT DO NOTHING"""),
                (content_id, *version.values(), key, now))
            cached = execute(conn, '''SELECT extraction_id FROM document_extractions
                WHERE content_id=? AND parser_name=? AND parser_version=? AND
                ocr_engine=? AND ocr_engine_version=? AND options_json=?''',
                (content_id, *version.values())).fetchone()
            if cached is not None:
                execute(conn, 'UPDATE document_processing_work SET extraction_id=? WHERE resource_key=?',
                             (cached['extraction_id'], key))
            result.append(dict(execute(conn, 'SELECT * FROM document_processing_work WHERE resource_key=?',
                                            (key,)).fetchone()))
        commit(conn)
        return result
    except BaseException:
        rollback(conn)
        raise


@atomic_repository('documents')
def claim_processing(conn, *, work_id, seconds=600, now=None):
    """Use the same fence as legacy extraction; no separate competing ownership.

    Consumers persist through the existing fenced extraction repository and then
    reconcile the exact version. A crash at either boundary leaves recoverable work.
    """
    if is_postgresql(conn):
        from ..postgresql_workers import claim_processing as native
        return native(conn, work_id=work_id, seconds=seconds, now=now)
    row = execute(conn, 'SELECT * FROM document_processing_work WHERE work_id=?',
                       (work_id,)).fetchone()
    if row is None:
        raise ValueError('Unknown processing work')
    if row['extraction_id'] is not None:
        raise ExtractionBusy('Processing already recorded; inspect its actual outcome')
    return claim_extraction(conn, row['resource_key'], seconds=seconds, now=now)


@atomic_repository('documents')
def automatic_processing_contents(conn, *, version, now, epoch, limit=2,
                                  historical_limit=0,
                                  artifact_types=('presentation', 'transcript'), artifact_ids=None, focus_company_ids=()):
    """Select supported inbox arrivals and at most one legacy recovery item.

    Old parser outcomes never become an implicit re-extraction campaign. Existing
    exact-version work can recover its cache after a crash; active fences skip.
    Unknown periods remain unknown and receive the late/unknown fresh lane.
    """
    from ...documents.priority import bounded_lanes, current_period
    from ...documents.availability import parse_utc
    parse_utc(epoch); parse_utc(now)
    if not artifact_types or set(artifact_types) - {'presentation','transcript','credit_rating'}:
        raise ValueError('Select supported document types')
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('Document batch limit must be 1–100')
    if type(historical_limit) is not int or not 0 <= historical_limit <= min(1, limit):
        raise ValueError('Historical capacity must be zero or one')
    # Per-lane LIMIT prevents old backlog or unsupported inbox rows masking new
    # arrivals. The same fence used by manual/legacy extraction controls claims.
    common = f'''SELECT c.content_id,c.created_at_utc first_observed_at_utc,a.period_end
        FROM artifact_contents c JOIN document_artifacts a USING(artifact_id)
        LEFT JOIN document_processing_inbox i USING(content_id)
        LEFT JOIN document_processing_work w ON w.content_id=c.content_id
          AND w.parser_name=? AND w.parser_version=? AND w.ocr_engine=?
          AND w.ocr_engine_version=? AND w.options_json=?
        WHERE a.artifact_type IN ({','.join('?' for _ in artifact_types)})
          AND (c.media_type IN ('application/pdf','application/vnd.ms-powerpoint',
             'application/vnd.openxmlformats-officedocument.presentationml.presentation')
             OR (c.media_type='text/html' AND a.artifact_type='credit_rating'))
          AND ((w.work_id IS NULL AND c.extraction_status='not_started')
               OR (w.work_id IS NOT NULL AND w.extraction_id IS NULL))
          AND NOT EXISTS (SELECT 1 FROM document_extraction_claims f
              WHERE f.resource_key=w.resource_key AND f.status='ACTIVE'
                AND f.lease_expires_at_utc>?)
          AND EXISTS (SELECT 1 FROM screener_collection_scope s
              WHERE s.company_id=a.company_id AND s.eligibility='eligible')'''
    params = (*version.values(), *artifact_types, now)
    if artifact_ids is not None:
        ids = tuple(dict.fromkeys(artifact_ids))
        if not 1 <= len(ids) <= 100 or any(type(x) is not int or x <= 0 for x in ids):
            raise ValueError('Select 1–100 positive artifact IDs')
        common += f" AND a.artifact_id IN ({','.join('?' for _ in ids)})"
        params += ids
    focus=tuple(sorted(set(focus_company_ids)))
    if len(focus)>20000 or any(type(i) is not int or i<1 for i in focus):
        raise ValueError('Focus company identifiers must be positive canonical IDs')
    focus_order=('CASE WHEN a.company_id IN ('+','.join(map(str,focus))+') THEN 0 ELSE 1 END,') if focus else ''
    rows = []
    for predicate, args in (
        ('i.content_id IS NOT NULL AND c.created_at_utc>=? AND a.period_end=?', (epoch,current_period(now))),
        ('i.content_id IS NOT NULL AND c.created_at_utc>=? AND (a.period_end IS NULL OR a.period_end<>?)', (epoch,current_period(now))),
        ('c.created_at_utc<?', (epoch,)),
    ):
        if predicate == 'c.created_at_utc<?' and not historical_limit:
            continue
        rows.extend(dict(r) for r in execute(conn, common+' AND '+predicate+
            ' ORDER BY '+focus_order+'c.created_at_utc,c.content_id LIMIT ?', (*params,*args,limit)))
    return bounded_lanes(rows,limit=limit,historical_limit=historical_limit,epoch=epoch,now=now)
