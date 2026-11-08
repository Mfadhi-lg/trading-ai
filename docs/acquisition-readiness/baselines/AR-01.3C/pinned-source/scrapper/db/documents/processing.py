"""Explicit bounded deterministic extraction handoff; no worker or corpus scan.

The inbox records all saved origins atomically. Unsupported media remain evidence,
not extraction jobs. Reconciliation must receive an operator-selected content set
and the exact parser/OCR/options contract; failed cache entries remain failed.
"""
from dataclasses import dataclass
from ..document_work_claims import claim_extraction, ExtractionBusy
from ...documents.extraction import canonical_json


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


def reconcile_processing(conn, *, content_ids, version, now):
    """Recover missed handoffs and post-persistence crashes for <=100 selected IDs.

    Requires explicitly applied 072. One short transaction; no filesystem, OCR,
    network, migration, default version or automatic re-extraction.
    """
    ids = tuple(dict.fromkeys(content_ids))
    if not 1 <= len(ids) <= 100 or any(type(x) is not int or x <= 0 for x in ids):
        raise ValueError('Select between 1 and 100 positive content IDs')
    if conn.in_transaction:
        raise ValueError('Reconciliation needs an idle connection')
    conn.execute('BEGIN IMMEDIATE')
    result = []
    try:
        for content_id in ids:
            content = conn.execute('''SELECT c.*, a.artifact_type FROM artifact_contents c
                JOIN document_artifacts a USING(artifact_id) WHERE c.content_id=?''',
                (content_id,)).fetchone()
            if content is None:
                raise ValueError(f'Unknown content {content_id}')
            conn.execute('INSERT OR IGNORE INTO document_processing_inbox VALUES (?,?)',
                         (content_id, content['created_at_utc']))
            supported = content['media_type'] in (
                'application/pdf', 'application/vnd.ms-powerpoint',
                'application/vnd.openxmlformats-officedocument.presentationml.presentation') or (
                content['media_type'] == 'text/html' and content['artifact_type'] == 'credit_rating')
            if not supported:
                continue
            key = version.key(content_id)
            conn.execute('''INSERT OR IGNORE INTO document_processing_work
                (content_id,parser_name,parser_version,ocr_engine,ocr_engine_version,
                 options_json,resource_key,created_at_utc) VALUES (?,?,?,?,?,?,?,?)''',
                (content_id, *version.values(), key, now))
            cached = conn.execute('''SELECT extraction_id FROM document_extractions
                WHERE content_id=? AND parser_name=? AND parser_version=? AND
                ocr_engine=? AND ocr_engine_version=? AND options_json=?''',
                (content_id, *version.values())).fetchone()
            if cached is not None:
                conn.execute('UPDATE document_processing_work SET extraction_id=? WHERE resource_key=?',
                             (cached['extraction_id'], key))
            result.append(dict(conn.execute('SELECT * FROM document_processing_work WHERE resource_key=?',
                                            (key,)).fetchone()))
        conn.commit()
        return result
    except BaseException:
        conn.rollback()
        raise


def claim_processing(conn, *, work_id, seconds=600, now=None):
    """Use the same fence as legacy extraction; no separate competing ownership.

    Consumers persist through the existing fenced extraction repository and then
    reconcile the exact version. A crash at either boundary leaves recoverable work.
    """
    row = conn.execute('SELECT * FROM document_processing_work WHERE work_id=?',
                       (work_id,)).fetchone()
    if row is None:
        raise ValueError('Unknown processing work')
    if row['extraction_id'] is not None:
        raise ExtractionBusy('Processing already recorded; inspect its actual outcome')
    return claim_extraction(conn, row['resource_key'], seconds=seconds, now=now)
