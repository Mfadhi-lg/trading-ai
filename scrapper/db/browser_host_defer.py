"""Administrative browser defer without fabricated transport attempts."""
import json
from datetime import datetime, timezone
from ..core.attempts import claim_attempt
from .host_permits import transaction
from .document_work_claims import OwnershipLost


def defer(conn, job, *, now, retry_at, dispatched, reason, completed_requests=()):
    stamp = datetime.fromtimestamp(now, timezone.utc).isoformat()
    deadline = datetime.fromtimestamp(retry_at, timezone.utc).isoformat()
    with transaction(conn):
        cursor = conn.execute("""UPDATE browser_rating_recovery_jobs
          SET status='PENDING',attempt_count=attempt_count-?,lease_owner=NULL,
            lease_expires_at_utc=NULL,error=?,evidence_json=?,updated_at_utc=?
          WHERE browser_job_id=? AND run_id=? AND status='RUNNING'
            AND lease_owner=? AND claim_token=? AND attempt_count=?
            AND lease_expires_at_utc>?""",
          (int(not dispatched), reason, json.dumps({'administrative_defer':reason,
            'retry_at':retry_at,'dispatched_requests':dispatched,
            'completed_requests':list(completed_requests)},sort_keys=True),
            stamp,job['browser_job_id'],job['run_id'],job['lease_owner'],
            job['claim_token'],claim_attempt(job,'browser'),stamp))
        if cursor.rowcount != 1:
            raise OwnershipLost('Cannot defer an expired or replaced browser claim')
        # Existing run/host waiting contract; no migration or queue history deletion.
        conn.execute("""INSERT INTO browser_rating_recovery_hosts
          (run_id,target_host,blocked_until_utc,consecutive_blocks,reason,updated_at_utc)
          VALUES(?,?,?,0,?,?) ON CONFLICT(run_id,target_host) DO UPDATE SET
          blocked_until_utc=max(blocked_until_utc,excluded.blocked_until_utc),
          reason=excluded.reason,updated_at_utc=excluded.updated_at_utc""",
          (job['run_id'],job['target_host'],deadline,reason,stamp))


def assert_unexpired(job, stamp):
    if job['lease_expires_at_utc'] <= stamp:
        raise OwnershipLost('Browser claim expired')


def active(conn, token):
    row = conn.execute('SELECT status FROM acquisition_host_permits WHERE permit_token=?',(token,)).fetchone()
    return row is not None and row[0] == 'ACTIVE'
