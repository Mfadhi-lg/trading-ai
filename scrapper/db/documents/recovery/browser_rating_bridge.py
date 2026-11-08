"""Read-only queries for the browser bridge; no transaction ownership."""


def cooldown(conn, run_id, now):
    return conn.execute('SELECT blocked_until_utc FROM browser_rating_recovery_hosts WHERE run_id=? AND target_host=? AND blocked_until_utc > ?', (run_id, 'www.icra.in', now)).fetchone()


def owned_job(conn, browser_job_id, run_id, owner):
    return conn.execute("""SELECT * FROM browser_rating_recovery_jobs
                WHERE browser_job_id=? AND run_id=? AND status='RUNNING' AND lease_owner=?""",
                (browser_job_id, run_id, owner)).fetchone()


def existing_content(conn, artifact_id):
    return conn.execute('SELECT content_id FROM artifact_contents WHERE artifact_id=? LIMIT 1', (artifact_id,)).fetchone()
