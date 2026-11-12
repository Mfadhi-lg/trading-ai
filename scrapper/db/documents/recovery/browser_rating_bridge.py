"""Read-only queries for the browser bridge; no transaction ownership."""
from scrapper.db.repository_backend import (execute, executemany, generated_id, statement, is_postgresql, atomic_repository, begin_immediate, commit, rollback, transaction as repository_transaction)


@atomic_repository('documents')
def cooldown(conn, run_id, now):
    return execute(conn, statement(conn, 'SELECT blocked_until_utc FROM browser_rating_recovery_hosts WHERE run_id=? AND target_host=? AND blocked_until_utc > ?', 'SELECT blocked_until_utc FROM browser_rating_recovery_hosts WHERE run_id=? AND target_host=? AND CAST(blocked_until_utc AS timestamptz) > CAST(? AS timestamptz)'), (run_id, 'www.icra.in', now)).fetchone()


@atomic_repository('documents')
def owned_job(conn, browser_job_id, run_id, owner):
    return execute(conn, """SELECT * FROM browser_rating_recovery_jobs
                WHERE browser_job_id=? AND run_id=? AND status='RUNNING' AND lease_owner=?""",
                (browser_job_id, run_id, owner)).fetchone()


@atomic_repository('documents')
def existing_content(conn, artifact_id):
    return execute(conn, 'SELECT content_id FROM artifact_contents WHERE artifact_id=? LIMIT 1', (artifact_id,)).fetchone()
