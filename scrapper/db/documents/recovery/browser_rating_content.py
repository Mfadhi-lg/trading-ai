"""Browser content lookup, queue reconciliation and processing evidence SQL.

Callers retain the existing transaction boundaries; no queue or claim redesign.
"""
from scrapper.core.attempts import claim_attempt
from scrapper.db.repository_backend import (execute, executemany, generated_id, statement, is_postgresql, atomic_repository, begin_immediate, commit, rollback, transaction as repository_transaction)

@atomic_repository('documents')
def existing_content(conn, artifact_id):
    existing = execute(conn, 'SELECT content_id FROM artifact_contents WHERE artifact_id=? LIMIT 1', (artifact_id,)).fetchone()
    return existing

@atomic_repository('documents')
def source_ids(conn, artifact_id, requested_url):
    ids = tuple((r[0] for r in execute(conn, 'SELECT artifact_source_id FROM artifact_sources\n        WHERE artifact_id=? AND document_url=?', (artifact_id, requested_url))))
    return ids

@atomic_repository('documents')
def reconcile_download(conn, artifact_id, now, *, job=None):
    with repository_transaction(conn):
        if job is not None:
            # Short write transaction only around fencing and reconciliation.
            begin_immediate(conn, lock='documents')
            owned = execute(conn, """SELECT 1 FROM browser_rating_recovery_jobs
                WHERE browser_job_id=? AND run_id=? AND status='RUNNING'
                AND lease_owner=? AND attempt_count=? AND claim_token=?""",
                (job['browser_job_id'], job['run_id'], job['lease_owner'],
                 claim_attempt(job, 'browser'), job['claim_token'])).fetchone()
            if owned is None:
                raise RuntimeError('Browser import lease is no longer owned by this claim')
        execute(conn, "UPDATE document_artifacts SET acquisition_status='downloaded' WHERE artifact_id=? AND acquisition_status <> 'validated'", (artifact_id,))
        execute(conn, "UPDATE credit_rating_download_queue SET status='SUCCEEDED',\n          last_result_status='OPERATOR_BROWSER_RECOVERED',last_error=NULL,\n          next_attempt_at_utc=NULL,lease_owner=NULL,lease_expires_at_utc=NULL,updated_at_utc=?\n          WHERE artifact_id=? AND status <> 'SUCCEEDED' ", (now, artifact_id))
        execute(conn, "UPDATE artifact_download_queue SET status='SKIPPED_ALREADY_HAVE_CONTENT',\n          last_result_status='OPERATOR_BROWSER_RECOVERED',last_error=NULL,\n          next_attempt_at_utc=NULL,lease_owner=NULL,lease_expires_at_utc=NULL,updated_at_utc=?\n          WHERE artifact_id=? AND status <> 'SUCCEEDED' ", (now, artifact_id))

@atomic_repository('documents')
def next_processing_job(conn, run_id):
    return execute(conn, statement(conn, "SELECT * FROM browser_rating_recovery_jobs\n            WHERE run_id=? AND status IN ('SUCCEEDED','SKIPPED_EXISTING') AND content_id IS NOT NULL\n            AND json_extract(evidence_json,'$.processing_status') IS NULL\n            ORDER BY browser_job_id LIMIT 1", """SELECT * FROM browser_rating_recovery_jobs
            WHERE run_id=? AND status IN ('SUCCEEDED','SKIPPED_EXISTING') AND content_id IS NOT NULL
            AND (CAST(evidence_json AS jsonb)->>'processing_status') IS NULL
            ORDER BY browser_job_id LIMIT 1"""), (run_id,)).fetchone()

@atomic_repository('documents')
def pending_count(conn, run_id):
    return execute(conn, "SELECT count(*) FROM browser_rating_recovery_jobs WHERE run_id=? AND status IN ('PENDING','RUNNING')", (run_id,)).fetchone()[0]

@atomic_repository('documents')
def save_processing_evidence(conn, browser_job_id, evidence_json, now):
    with repository_transaction(conn):
        execute(conn, 'UPDATE browser_rating_recovery_jobs SET evidence_json=?,updated_at_utc=? WHERE browser_job_id=?', (evidence_json, now, browser_job_id))

@atomic_repository('documents')
def html_jobs(conn, run_id):
    return execute(conn, "SELECT job.*,content.content_origin FROM browser_rating_recovery_jobs job\n        JOIN artifact_contents content ON content.content_id=job.content_id\n        WHERE job.run_id=? AND job.status IN ('SUCCEEDED','SKIPPED_EXISTING')\n        AND content.media_type='text/html' ORDER BY job.browser_job_id", (run_id,)).fetchall()
