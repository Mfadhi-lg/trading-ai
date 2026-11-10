"""Browser recovery ledger persistence; transaction and lease semantics retained."""
from scrapper.core.attempts import claim_attempt
from datetime import datetime, timedelta, timezone
import json
import secrets

from ...repo import utc_now
from scrapper.db.repository_backend import (execute, executemany, generated_id, statement, is_postgresql, atomic_repository, begin_immediate, commit, rollback, transaction as repository_transaction)

@atomic_repository('documents')
def worker_state(conn, run_id, owner, status, *, utc_now=utc_now):
    with repository_transaction(conn):
        execute(conn, '''INSERT INTO browser_rating_recovery_workers
            (worker_id,run_id,status,heartbeat_at_utc,stopped_at_utc) VALUES(?,?,?,?,?)
            ON CONFLICT(worker_id) DO UPDATE SET status=excluded.status,
            heartbeat_at_utc=excluded.heartbeat_at_utc,stopped_at_utc=excluded.stopped_at_utc''',
            (owner, run_id, status, utc_now(), utc_now() if status == 'STOPPED' else None))


@atomic_repository('documents')
def release_owned_jobs(conn, run_id, owner, *, utc_now=utc_now):
    with repository_transaction(conn):
        return execute(conn, '''UPDATE browser_rating_recovery_jobs SET status='PENDING',
            lease_owner=NULL,lease_expires_at_utc=NULL,updated_at_utc=?
            WHERE run_id=? AND status='RUNNING' AND lease_owner=?''',
            (utc_now(), run_id, owner)).rowcount


@atomic_repository('documents')
def cool_down(conn, run_id, host, retry_after=None, *, utc_now=utc_now, datetime=datetime, retry_delay):
    row = execute(conn, 'SELECT consecutive_blocks FROM browser_rating_recovery_hosts WHERE run_id=? AND target_host=?', (run_id, host)).fetchone()
    consecutive = int(row[0]) + 1 if row else 1
    until = (datetime.now(timezone.utc) + timedelta(seconds=retry_delay(retry_after, consecutive))).isoformat()
    with repository_transaction(conn):
        execute(conn, '''INSERT INTO browser_rating_recovery_hosts
          (run_id,target_host,blocked_until_utc,last_http_status,consecutive_blocks,reason,updated_at_utc)
          VALUES (?,?,?,429,?,'PUBLISHER_RATE_LIMIT',?)
          ON CONFLICT(run_id,target_host) DO UPDATE SET blocked_until_utc=excluded.blocked_until_utc,
            last_http_status=429,consecutive_blocks=excluded.consecutive_blocks,
            reason=excluded.reason,updated_at_utc=excluded.updated_at_utc''',
          (run_id,host,until,consecutive,utc_now()))
    return until


@atomic_repository('documents')
def seed(conn, run_id, *, utc_now=utc_now, agency_hosts, settled):
    now = utc_now()
    with repository_transaction(conn):
        cursor = execute(conn, statement(conn, f'''INSERT OR IGNORE INTO browser_rating_recovery_jobs
          (run_id,artifact_id,artifact_source_id,requested_url,target_host,
           company_name,created_at_utc,updated_at_utc)
          SELECT ?, a.artifact_id,min(s.artifact_source_id),q.requested_url,
                 q.target_host,c.current_legal_name,?,?
          FROM (
            SELECT artifact_id,requested_url,target_host,status FROM credit_rating_download_queue
            UNION ALL
            SELECT artifact_id,requested_url,target_host,status FROM artifact_download_queue
          ) q JOIN document_artifacts a USING(artifact_id)
          JOIN companies c USING(company_id)
          JOIN artifact_sources s ON s.artifact_id=q.artifact_id AND s.document_url=q.requested_url
          WHERE a.artifact_type='credit_rating'
          AND q.target_host IN ({','.join('?' for _ in agency_hosts)})
          AND q.status IN ({','.join('?' for _ in settled)})
          AND NOT EXISTS(SELECT 1 FROM artifact_contents ac WHERE ac.artifact_id=q.artifact_id)
          GROUP BY a.artifact_id,q.requested_url,q.target_host
          ORDER BY coalesce(a.event_date,'') DESC,a.artifact_id DESC''', f"INSERT INTO browser_rating_recovery_jobs\n          (run_id,artifact_id,artifact_source_id,requested_url,target_host,\n           company_name,created_at_utc,updated_at_utc)\n          SELECT ?, a.artifact_id,min(s.artifact_source_id),q.requested_url,\n                 q.target_host,c.current_legal_name,?,?\n          FROM (\n            SELECT artifact_id,requested_url,target_host,status FROM credit_rating_download_queue\n            UNION ALL\n            SELECT artifact_id,requested_url,target_host,status FROM artifact_download_queue\n          ) q JOIN document_artifacts a USING(artifact_id)\n          JOIN companies c USING(company_id)\n          JOIN artifact_sources s ON s.artifact_id=q.artifact_id AND s.document_url=q.requested_url\n          WHERE a.artifact_type='credit_rating'\n          AND q.target_host IN ({','.join(('?' for _ in agency_hosts))})\n          AND q.status IN ({','.join(('?' for _ in settled))})\n          AND NOT EXISTS(SELECT 1 FROM artifact_contents ac WHERE ac.artifact_id=q.artifact_id)\n          GROUP BY a.artifact_id,q.requested_url,q.target_host,c.current_legal_name\n          ORDER BY coalesce(a.event_date,'') DESC,a.artifact_id DESC ON CONFLICT DO NOTHING"),
          (run_id, now, now, *agency_hosts, *settled))
    return cursor.rowcount


@atomic_repository('documents')
def report(conn, run_id):
    return [dict(r) for r in execute(conn, '''SELECT target_host,status,count(*) jobs,
        sum(attempt_count) attempts FROM browser_rating_recovery_jobs
        WHERE run_id=? GROUP BY target_host,status ORDER BY target_host,status''', (run_id,))]


@atomic_repository('documents')
def claim(conn, run_id, hosts, owner, job_ids=(), *, utc_now=utc_now, datetime=datetime):
    now = utc_now()
    expires = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
    begin_immediate(conn, lock='documents')
    try:
        execute(conn, statement(conn, '''UPDATE browser_rating_recovery_jobs SET status='PENDING',
            lease_owner=NULL,lease_expires_at_utc=NULL WHERE run_id=?
            AND status='RUNNING' AND lease_expires_at_utc < ?''', "UPDATE browser_rating_recovery_jobs SET status='PENDING',\n            lease_owner=NULL,lease_expires_at_utc=NULL WHERE run_id=?\n            AND status='RUNNING' AND CAST(lease_expires_at_utc AS timestamptz) < CAST(? AS timestamptz)"), (run_id, now))
        host_clause = f" AND target_host IN ({','.join('?' for _ in hosts)})" if hosts else ''
        job_clause = f" AND browser_job_id IN ({','.join('?' for _ in job_ids)})" if job_ids else ''
        row = execute(conn, statement(conn, f'''SELECT * FROM browser_rating_recovery_jobs
            WHERE run_id=? AND status='PENDING' {host_clause} {job_clause}
            AND NOT EXISTS(SELECT 1 FROM browser_rating_recovery_hosts health
              WHERE health.run_id=browser_rating_recovery_jobs.run_id
              AND health.target_host=browser_rating_recovery_jobs.target_host
              AND health.blocked_until_utc > ?)
            ORDER BY browser_job_id LIMIT 1''', f"SELECT * FROM browser_rating_recovery_jobs\n            WHERE run_id=? AND status='PENDING' {host_clause} {job_clause}\n            AND NOT EXISTS(SELECT 1 FROM browser_rating_recovery_hosts health\n              WHERE health.run_id=browser_rating_recovery_jobs.run_id\n              AND health.target_host=browser_rating_recovery_jobs.target_host\n              AND CAST(health.blocked_until_utc AS timestamptz) > CAST(? AS timestamptz))\n            ORDER BY browser_job_id LIMIT 1"), (run_id, *hosts, *job_ids, now)).fetchone()
        if row:
            token = secrets.token_hex(16)
            execute(conn, '''UPDATE browser_rating_recovery_jobs SET status='RUNNING',
                attempt_count=attempt_count+1,lease_owner=?,lease_expires_at_utc=?,
                updated_at_utc=?,claim_token=? WHERE browser_job_id=?''',
                (owner, expires, now, token, row['browser_job_id']))
        # Keep the legacy pre-increment attempt_count for bounded retry callers,
        # but return the owner of this claim, not the pre-claim NULL owner.
        if row:
            row = dict(row)
            row['lease_owner'] = owner
            row['claim_token'] = token
        commit(conn)
        return row
    except Exception:
        rollback(conn)
        raise


@atomic_repository('documents')
def finish(conn, job, status, content_id=None, final_url=None, error=None, evidence=None, *, utc_now=utc_now):
    with repository_transaction(conn):
        cursor = execute(conn, '''UPDATE browser_rating_recovery_jobs SET status=?,content_id=?,
          final_url=?,error=?,evidence_json=?,lease_owner=NULL,lease_expires_at_utc=NULL,
          updated_at_utc=? WHERE browser_job_id=? AND run_id=? AND status='RUNNING'
          AND lease_owner=? AND attempt_count=? AND claim_token=?''',
          (status, content_id, final_url, error, json.dumps(evidence or {}, sort_keys=True),
           utc_now(), job['browser_job_id'], job['run_id'], job['lease_owner'],
           claim_attempt(job, 'browser'), job['claim_token']))
        if cursor.rowcount != 1:
            raise RuntimeError('Browser job lease is no longer owned by this claim')


