"""Short, fenced renewal and extraction ownership transactions. No worker threads."""
from datetime import datetime, timedelta, timezone
from secrets import token_hex as extraction_token
from ..core.attempts import claim_attempt

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


def renew_queue(conn, kind, job, *, seconds, now=None):
    table, key, status = QUEUES[kind]
    now = now or utc_now()
    try:
        count = claim_attempt(job, kind)
    except ValueError as exc:
        raise OwnershipLost("Invalid captured queue claim") from exc
    with conn:
        result = conn.execute(f"""UPDATE {table}
            SET lease_expires_at_utc=max(lease_expires_at_utc, ?)
            WHERE {key}=? AND status=? AND lease_owner=?
            AND claim_token=? AND attempt_count=? AND lease_expires_at_utc>?""",
            (expiry(now, seconds), job[key], status, job['lease_owner'],
             job['claim_token'], count, now))
        if result.rowcount != 1:
            raise OwnershipLost('Cannot renew an expired or replaced queue claim')


def claim_extraction(conn, resource_key, *, seconds=600, now=None):
    now = now or utc_now()
    token = extraction_token(16)
    conn.execute('BEGIN IMMEDIATE')
    try:
        row = conn.execute('SELECT * FROM document_extraction_claims WHERE resource_key=?',
                           (resource_key,)).fetchone()
        if row and row['status'] == 'ACTIVE' and row['lease_expires_at_utc'] > now:
            raise ExtractionBusy('Extraction is already owned by an active worker')
        conn.execute("""INSERT INTO document_extraction_claims VALUES(?,?,?,'ACTIVE',?)
            ON CONFLICT(resource_key) DO UPDATE SET owner=excluded.owner,
            claim_token=excluded.claim_token,status='ACTIVE',
            lease_expires_at_utc=excluded.lease_expires_at_utc""",
            (resource_key, token, token, expiry(now, seconds)))
        claim = dict(conn.execute('SELECT * FROM document_extraction_claims WHERE resource_key=?',
                                  (resource_key,)).fetchone())
        conn.commit()
        return claim
    except BaseException:
        conn.rollback()
        raise


def assert_extraction_owned(conn, claim, *, now=None):
    row = conn.execute("""SELECT 1 FROM document_extraction_claims
        WHERE resource_key=? AND owner=? AND claim_token=? AND status='ACTIVE'
        AND lease_expires_at_utc>?""",
        (claim['resource_key'],claim['owner'],claim['claim_token'],now or utc_now())).fetchone()
    if row is None:
        raise OwnershipLost('Extraction claim is expired or replaced')


def renew_extraction(conn, claim, *, seconds=600, now=None):
    now = now or utc_now()
    with conn:
        result = conn.execute("""UPDATE document_extraction_claims
            SET lease_expires_at_utc=max(lease_expires_at_utc,?)
            WHERE resource_key=? AND owner=? AND claim_token=? AND status='ACTIVE'
            AND lease_expires_at_utc>?""",
            (expiry(now,seconds),claim['resource_key'],claim['owner'],claim['claim_token'],now))
        if result.rowcount != 1:
            raise OwnershipLost('Cannot renew an expired or replaced extraction claim')


def release_extraction(conn, claim):
    # May be called inside the extraction persistence transaction. Do not commit here.
    return conn.execute("""UPDATE document_extraction_claims SET status='RELEASED'
        WHERE resource_key=? AND owner=? AND claim_token=? AND status='ACTIVE'""",
        (claim['resource_key'],claim['owner'],claim['claim_token'])).rowcount
