-- A finite, explicitly operator-authorized recovery run. Normal host policy
-- and the normal worker queues retain their existing access rules.
CREATE TABLE IF NOT EXISTS browser_rating_recovery_jobs (
    browser_job_id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    artifact_id INTEGER NOT NULL REFERENCES document_artifacts(artifact_id),
    artifact_source_id INTEGER NOT NULL REFERENCES artifact_sources(artifact_source_id),
    requested_url TEXT NOT NULL,
    target_host TEXT NOT NULL,
    company_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (status IN (
      'PENDING','RUNNING','SUCCEEDED','FAILED','REVIEW_REQUIRED','SKIPPED_EXISTING'
    )),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    lease_owner TEXT,
    lease_expires_at_utc TEXT,
    content_id INTEGER REFERENCES artifact_contents(content_id),
    final_url TEXT,
    error TEXT,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    UNIQUE(run_id,artifact_id,requested_url)
);
CREATE INDEX IF NOT EXISTS idx_browser_rating_recovery_pending
ON browser_rating_recovery_jobs(run_id,target_host,status,browser_job_id);
