"""Native PostgreSQL extraction ownership and bounded processing handoff.

Use only in a transaction leased through ``postgresql.connect`` or its pool.
Functions never commit: the caller must commit the short claim transaction
before doing OCR or external work. Persistence must call
``assert_extraction_owned`` and write/release in the same transaction; its row
lock prevents a competing reclaimer from replacing ownership during the write.
This module does not admit downloads or bypass their publisher/host policies.
"""
from datetime import datetime, timedelta, timezone
from secrets import token_hex

from .document_work_claims import ExtractionBusy, OwnershipLost


def _transaction(conn):
    from psycopg.pq import TransactionStatus
    if conn.info.transaction_status != TransactionStatus.INTRANS:
        raise ValueError('A managed PostgreSQL transaction is required')


def _instant(value=None):
    instant = datetime.now(timezone.utc) if value is None else datetime.fromisoformat(value)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError('An explicit timezone is required')
    return instant.astimezone(timezone.utc)


def _deadline(now, seconds):
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 0 < seconds < float('inf'):
        raise ValueError('Lease seconds must be positive and finite')
    return (now + timedelta(seconds=seconds)).isoformat()


def claim_extraction(conn, resource_key, *, seconds=600, now=None):
    """Atomically claim an absent, released or expired resource, including races."""
    _transaction(conn)
    if not isinstance(resource_key, str) or not resource_key:
        raise ValueError('A nonempty resource key is required')
    instant = _instant(now)
    deadline = _deadline(instant, seconds)
    token = token_hex(16)
    row = conn.execute('''INSERT INTO document_extraction_claims
        (resource_key,owner,claim_token,status,lease_expires_at_utc)
        VALUES (%s,%s,%s,'ACTIVE',%s)
        ON CONFLICT(resource_key) DO UPDATE SET owner=EXCLUDED.owner,
          claim_token=EXCLUDED.claim_token,status='ACTIVE',
          lease_expires_at_utc=EXCLUDED.lease_expires_at_utc
        WHERE document_extraction_claims.status='RELEASED'
          OR document_extraction_claims.lease_expires_at_utc::timestamptz <= %s::timestamptz
        RETURNING *''', (resource_key, token, token, deadline, instant.isoformat())).fetchone()
    if row is None:
        raise ExtractionBusy('Extraction is already owned by an active worker')
    return dict(row)


def assert_extraction_owned(conn, claim, *, now=None):
    """Check and lock ownership until the enclosing persistence transaction ends."""
    _transaction(conn)
    row = conn.execute('''SELECT resource_key FROM document_extraction_claims
        WHERE resource_key=%s AND owner=%s AND claim_token=%s AND status='ACTIVE'
          AND lease_expires_at_utc::timestamptz > %s::timestamptz
        FOR UPDATE''', (claim['resource_key'], claim['owner'], claim['claim_token'],
                        _instant(now).isoformat())).fetchone()
    if row is None:
        raise OwnershipLost('Extraction claim is expired or replaced')


def renew_extraction(conn, claim, *, seconds=600, now=None):
    _transaction(conn)
    instant = _instant(now)
    deadline = _deadline(instant, seconds)
    row = conn.execute('''UPDATE document_extraction_claims
        SET lease_expires_at_utc=CASE
          WHEN lease_expires_at_utc::timestamptz >= %s::timestamptz
          THEN lease_expires_at_utc ELSE %s END
        WHERE resource_key=%s AND owner=%s AND claim_token=%s AND status='ACTIVE'
          AND lease_expires_at_utc::timestamptz > %s::timestamptz
        RETURNING resource_key''', (deadline, deadline, claim['resource_key'],
            claim['owner'], claim['claim_token'], instant.isoformat())).fetchone()
    if row is None:
        raise OwnershipLost('Cannot renew an expired or replaced extraction claim')


def release_extraction(conn, claim):
    _transaction(conn)
    return conn.execute('''UPDATE document_extraction_claims SET status='RELEASED'
        WHERE resource_key=%s AND owner=%s AND claim_token=%s AND status='ACTIVE' ''',
        (claim['resource_key'], claim['owner'], claim['claim_token'])).rowcount


def reconcile_processing(conn, *, content_ids, version, now):
    """Port of bounded processing reconciliation; all-or-nothing in a savepoint.

    Exact version/cache identity, media support, unsupported inbox evidence and
    failed outcomes follow ``documents.processing.reconcile_processing``.
    """
    _transaction(conn)
    ids = tuple(dict.fromkeys(content_ids))
    if not 1 <= len(ids) <= 100 or any(type(x) is not int or x <= 0 for x in ids):
        raise ValueError('Select between 1 and 100 positive content IDs')
    _instant(now)
    result = []
    with conn.transaction():
        for content_id in ids:
            content = conn.execute('''SELECT c.*,a.artifact_type FROM artifact_contents c
                JOIN document_artifacts a USING(artifact_id) WHERE c.content_id=%s''',
                (content_id,)).fetchone()
            if content is None:
                raise ValueError(f'Unknown content {content_id}')
            conn.execute('''INSERT INTO document_processing_inbox VALUES (%s,%s)
                ON CONFLICT(content_id) DO NOTHING''', (content_id, content['created_at_utc']))
            supported = content['media_type'] in (
                'application/pdf', 'application/vnd.ms-powerpoint',
                'application/vnd.openxmlformats-officedocument.presentationml.presentation') or (
                content['media_type'] == 'text/html' and content['artifact_type'] == 'credit_rating')
            if not supported:
                continue
            key = version.key(content_id)
            conn.execute('''INSERT INTO document_processing_work
                (content_id,parser_name,parser_version,ocr_engine,ocr_engine_version,
                 options_json,resource_key,created_at_utc) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT DO NOTHING''', (content_id, *version.values(), key, now))
            cached = conn.execute('''SELECT extraction_id FROM document_extractions
                WHERE content_id=%s AND parser_name=%s AND parser_version=%s AND
                ocr_engine=%s AND ocr_engine_version=%s AND options_json=%s''',
                (content_id, *version.values())).fetchone()
            if cached is not None:
                conn.execute('UPDATE document_processing_work SET extraction_id=%s WHERE resource_key=%s',
                             (cached['extraction_id'], key))
            result.append(dict(conn.execute('SELECT * FROM document_processing_work WHERE resource_key=%s',
                                            (key,)).fetchone()))
    return result


def claim_processing(conn, *, work_id, seconds=600, now=None):
    """Share the exact resource fence used by explicit/legacy extraction work."""
    _transaction(conn)
    row = conn.execute('SELECT * FROM document_processing_work WHERE work_id=%s',
                       (work_id,)).fetchone()
    if row is None:
        raise ValueError('Unknown processing work')
    if row['extraction_id'] is not None:
        raise ExtractionBusy('Processing already recorded; inspect its actual outcome')
    return claim_extraction(conn, row['resource_key'], seconds=seconds, now=now)
