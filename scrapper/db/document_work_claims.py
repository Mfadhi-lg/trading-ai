"""Short, fenced renewal and extraction ownership transactions. No worker threads."""
from datetime import datetime, timedelta, timezone
from secrets import token_hex as extraction_token
from ..core.attempts import claim_attempt
from .repository_backend import is_postgresql
from scrapper.db.repository_backend import (execute, executemany, generated_id, statement, is_postgresql, atomic_repository, begin_immediate, commit, rollback, transaction as repository_transaction)

def utc_now():
    return datetime.now(timezone.utc).isoformat()


class OwnershipLost(RuntimeError):
    pass


class ExtractionBusy(RuntimeError):
    pass


QUEUES = {
    'generic': ('artifact_download_queue', 'download_job_id', 'LEASED'),
    'rating': ('credit_rating_download_queue', 'download_job_id', 'LEASED'),
    'browser': ('browser_rating_recovery_jobs', 'browser_job_id', 'RUNNING'),
}


def expiry(now, seconds):
    if seconds <= 0:
        raise ValueError('lease seconds must be positive')
    return (datetime.fromisoformat(now) + timedelta(seconds=seconds)).isoformat()


@atomic_repository('documents')
def renew_queue(conn, kind, job, *, seconds, now=None):
    table, key, status = QUEUES[kind]
    now = now or utc_now()
    try:
        count = claim_attempt(job, kind)
    except ValueError as exc:
        raise OwnershipLost("Invalid captured queue claim") from exc
    with repository_transaction(conn):
        result = execute(conn, statement(conn, f"""UPDATE {table}
            SET lease_expires_at_utc=max(lease_expires_at_utc, ?)
            WHERE {key}=? AND status=? AND lease_owner=?
            AND claim_token=? AND attempt_count=? AND lease_expires_at_utc>?""", f'UPDATE {table}\n            SET lease_expires_at_utc=CASE WHEN lease_expires_at_utc::timestamptz >= renewal.deadline::timestamptz THEN lease_expires_at_utc ELSE renewal.deadline END FROM (SELECT ?::text AS deadline) renewal\n            WHERE {key}=? AND status=? AND lease_owner=?\n            AND claim_token=? AND attempt_count=? AND CAST(lease_expires_at_utc AS timestamptz) > CAST(? AS timestamptz)'),
            (expiry(now, seconds), job[key], status, job['lease_owner'],
             job['claim_token'], count, now))
        if result.rowcount != 1:
            raise OwnershipLost('Cannot renew an expired or replaced queue claim')


@atomic_repository('documents')
def claim_extraction(conn, resource_key, *, seconds=600, now=None):
    if is_postgresql(conn):
        from .postgresql_workers import claim_extraction as native
        return native(conn, resource_key, seconds=seconds, now=now)
    now = now or utc_now()
    token = extraction_token(16)
    begin_immediate(conn, lock='documents')
    try:
        row = execute(conn, 'SELECT * FROM document_extraction_claims WHERE resource_key=?',
                           (resource_key,)).fetchone()
        if row and row['status'] == 'ACTIVE' and row['lease_expires_at_utc'] > now:
            raise ExtractionBusy('Extraction is already owned by an active worker')
        execute(conn, """INSERT INTO document_extraction_claims VALUES(?,?,?,'ACTIVE',?)
            ON CONFLICT(resource_key) DO UPDATE SET owner=excluded.owner,
            claim_token=excluded.claim_token,status='ACTIVE',
            lease_expires_at_utc=excluded.lease_expires_at_utc""",
            (resource_key, token, token, expiry(now, seconds)))
        claim = dict(execute(conn, 'SELECT * FROM document_extraction_claims WHERE resource_key=?',
                                  (resource_key,)).fetchone())
        commit(conn)
        return claim
    except BaseException:
        rollback(conn)
        raise


@atomic_repository('documents')
def assert_extraction_owned(conn, claim, *, now=None):
    if is_postgresql(conn):
        from .postgresql_workers import assert_extraction_owned as native
        return native(conn, claim, now=now)
    row = execute(conn, statement(conn, """SELECT 1 FROM document_extraction_claims
        WHERE resource_key=? AND owner=? AND claim_token=? AND status='ACTIVE'
        AND lease_expires_at_utc>?""", "SELECT 1 FROM document_extraction_claims\n        WHERE resource_key=? AND owner=? AND claim_token=? AND status='ACTIVE'\n        AND CAST(lease_expires_at_utc AS timestamptz) > CAST(? AS timestamptz)"),
        (claim['resource_key'],claim['owner'],claim['claim_token'],now or utc_now())).fetchone()
    if row is None:
        raise OwnershipLost('Extraction claim is expired or replaced')


@atomic_repository('documents')
def renew_extraction(conn, claim, *, seconds=600, now=None):
    if is_postgresql(conn):
        from .postgresql_workers import renew_extraction as native
        return native(conn, claim, seconds=seconds, now=now)
    now = now or utc_now()
    with repository_transaction(conn):
        result = execute(conn, statement(conn, """UPDATE document_extraction_claims
            SET lease_expires_at_utc=max(lease_expires_at_utc,?)
            WHERE resource_key=? AND owner=? AND claim_token=? AND status='ACTIVE'
            AND lease_expires_at_utc>?""", "UPDATE document_extraction_claims\n            SET lease_expires_at_utc=CASE WHEN lease_expires_at_utc::timestamptz >= renewal.deadline::timestamptz THEN lease_expires_at_utc ELSE renewal.deadline END FROM (SELECT ?::text AS deadline) renewal\n            WHERE resource_key=? AND owner=? AND claim_token=? AND status='ACTIVE'\n            AND CAST(lease_expires_at_utc AS timestamptz) > CAST(? AS timestamptz)"),
            (expiry(now,seconds),claim['resource_key'],claim['owner'],claim['claim_token'],now))
        if result.rowcount != 1:
            raise OwnershipLost('Cannot renew an expired or replaced extraction claim')


@atomic_repository('documents')
def release_extraction(conn, claim):
    if is_postgresql(conn):
        from .postgresql_workers import release_extraction as native
        return native(conn, claim)
    # May be called inside the extraction persistence transaction. Do not commit here.
    return execute(conn, """UPDATE document_extraction_claims SET status='RELEASED'
        WHERE resource_key=? AND owner=? AND claim_token=? AND status='ACTIVE'""",
        (claim['resource_key'],claim['owner'],claim['claim_token'])).rowcount
