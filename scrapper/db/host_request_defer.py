"""Fenced administrative release; a wait before dispatch spends no retry."""
from datetime import datetime, timezone
from .document_work_claims import QUEUES, OwnershipLost
from .host_permits import transaction
from scrapper.db.repository_backend import (execute, executemany, generated_id, statement, is_postgresql, atomic_repository, begin_immediate, commit, rollback, transaction as repository_transaction)


@atomic_repository('documents')
def defer_job(conn, kind, job, *, now, retry_at, dispatched, reason):
    if kind not in {'generic', 'rating'}:
        raise ValueError('Only generic/rating HTTP claims are integrated')
    table, key, status = QUEUES[kind]
    policy_blocked = reason == 'PUBLISHER_POLICY_BLOCKED'
    next_status = 'SKIPPED_POLICY' if policy_blocked else 'RETRYABLE_FAILURE'
    deadline = None if policy_blocked else datetime.fromtimestamp(retry_at, timezone.utc).isoformat()
    with transaction(conn):
        cursor = execute(conn, statement(conn, f"""UPDATE {table}
            SET status=?, next_attempt_at_utc=?,
                lease_owner=NULL, lease_expires_at_utc=NULL,
                attempt_count=attempt_count-?, last_result_status=?,
                last_error=NULL, updated_at_utc=?
            WHERE {key}=? AND status=? AND lease_owner=? AND claim_token=?
                AND attempt_count=? AND lease_expires_at_utc>?""", f'UPDATE {table}\n            SET status=?, next_attempt_at_utc=?,\n                lease_owner=NULL, lease_expires_at_utc=NULL,\n                attempt_count=attempt_count-?, last_result_status=?,\n                last_error=NULL, updated_at_utc=?\n            WHERE {key}=? AND status=? AND lease_owner=? AND claim_token=?\n                AND attempt_count=? AND CAST(lease_expires_at_utc AS timestamptz) > CAST(? AS timestamptz)'),
            (next_status, deadline, int(not dispatched), reason, now, job[key], status,
             job['lease_owner'], job['claim_token'], job['attempt_count'], now))
        if cursor.rowcount != 1:
            raise OwnershipLost('Cannot defer an expired or replaced claim')


@atomic_repository('documents')
def require_shared_schema(conn):
    # Fail before claiming work; never auto-migrate or downgrade to old transport.
    import sys
    if sys.platform != 'linux':
        raise RuntimeError('Shared HTTP requires Linux parent-death fencing')
    execute(conn, 'SELECT permit_token,publisher_group,host,acquisition_mode,owner,status,acquired_at,lease_expires_at,released_at,policy_version,authorization_json FROM acquisition_host_permits LIMIT 0')
    execute(conn, 'SELECT publisher_group,next_request_at,blocked_until,circuit_until,observed_at,consecutive_failures,retry_after_raw,last_outcome,policy_version FROM acquisition_host_state LIMIT 0')
    if execute(conn, "SELECT 1 FROM schema_migrations WHERE version='071_shared_host_permits.sql'").fetchone() is None:
        raise RuntimeError('Shared HTTP requires explicitly applied migration 071')
