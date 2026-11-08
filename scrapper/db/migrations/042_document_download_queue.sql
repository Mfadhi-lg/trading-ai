-- DA-05: explicit download scopes and a durable, resumable artifact queue.

CREATE TABLE IF NOT EXISTS artifact_download_scopes (
    scope_id TEXT PRIMARY KEY,
    scope_kind TEXT NOT NULL CHECK (
      scope_kind IN ('P0', 'P1', 'P2', 'P3', 'custom')
    ),
    description TEXT,
    artifact_types_json TEXT NOT NULL,
    policy_json TEXT NOT NULL DEFAULT '{}',
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifact_download_scope_companies (
    scope_id TEXT NOT NULL
      REFERENCES artifact_download_scopes(scope_id) ON DELETE CASCADE,
    company_id INTEGER NOT NULL REFERENCES companies(company_id),
    symbol TEXT NOT NULL,
    outcome_label TEXT,
    archetype TEXT,
    PRIMARY KEY(scope_id, company_id)
);

CREATE TABLE IF NOT EXISTS artifact_download_queue (
    download_job_id INTEGER PRIMARY KEY,
    artifact_id INTEGER NOT NULL REFERENCES document_artifacts(artifact_id),
    requested_url TEXT NOT NULL,
    target_host TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    scope_id TEXT NOT NULL
      REFERENCES artifact_download_scopes(scope_id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (
      status IN (
        'PENDING', 'LEASED', 'SUCCEEDED', 'RETRYABLE_FAILURE',
        'TERMINAL_FAILURE', 'SKIPPED_ALREADY_HAVE_CONTENT', 'SKIPPED_POLICY'
      )
    ),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_attempt_at_utc TEXT,
    lease_owner TEXT,
    lease_expires_at_utc TEXT,
    last_result_status TEXT,
    last_error TEXT,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    UNIQUE(artifact_id, requested_url, scope_id)
);

CREATE INDEX IF NOT EXISTS idx_artifact_download_queue_claim
ON artifact_download_queue(
  scope_id, status, next_attempt_at_utc, priority, download_job_id
);

CREATE INDEX IF NOT EXISTS idx_artifact_download_queue_host
ON artifact_download_queue(target_host, status, lease_expires_at_utc);

CREATE INDEX IF NOT EXISTS idx_artifact_download_scope_company
ON artifact_download_scope_companies(company_id, scope_id);

CREATE TABLE IF NOT EXISTS artifact_download_scope_artifacts (
    scope_id TEXT NOT NULL
      REFERENCES artifact_download_scopes(scope_id) ON DELETE CASCADE,
    artifact_id INTEGER NOT NULL REFERENCES document_artifacts(artifact_id),
    PRIMARY KEY(scope_id, artifact_id)
);

CREATE INDEX IF NOT EXISTS idx_artifact_download_scope_artifact
ON artifact_download_scope_artifacts(artifact_id, scope_id);

CREATE TABLE IF NOT EXISTS artifact_download_queue_attempts (
    download_job_id INTEGER NOT NULL
      REFERENCES artifact_download_queue(download_job_id) ON DELETE CASCADE,
    download_attempt_id INTEGER NOT NULL UNIQUE
      REFERENCES artifact_download_attempts(download_attempt_id),
    PRIMARY KEY(download_job_id, download_attempt_id)
);

CREATE INDEX IF NOT EXISTS idx_artifact_download_queue_attempt_job
ON artifact_download_queue_attempts(download_job_id, download_attempt_id);
