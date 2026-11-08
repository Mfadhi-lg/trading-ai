-- Credit-rating-first promotion, resolution provenance, and resumable queue.

CREATE TABLE IF NOT EXISTS document_link_promotions (
    document_link_id INTEGER PRIMARY KEY
      REFERENCES document_links(document_link_id) ON DELETE CASCADE,
    artifact_id INTEGER REFERENCES document_artifacts(artifact_id),
    artifact_source_id INTEGER REFERENCES artifact_sources(artifact_source_id),
    promotion_status TEXT NOT NULL CHECK (
      promotion_status IN (
        'PROMOTED', 'SKIPPED_DEFERRED_TYPE', 'SKIPPED_GENERATED_SUMMARY',
        'SKIPPED_UNAVAILABLE_SLOT', 'QUARANTINED_UNKNOWN_TYPE',
        'QUARANTINED_INVALID_URL', 'QUARANTINED_IDENTITY_AMBIGUOUS'
      )
    ),
    promotion_reason TEXT NOT NULL,
    normalized_url TEXT,
    artifact_type TEXT,
    promoter_version TEXT NOT NULL,
    promoted_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_document_link_promotions_status
ON document_link_promotions(promotion_status, artifact_type);

CREATE TABLE IF NOT EXISTS credit_rating_download_queue (
    download_job_id INTEGER PRIMARY KEY,
    artifact_id INTEGER NOT NULL REFERENCES document_artifacts(artifact_id),
    requested_url TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK (
      status IN (
        'PENDING', 'LEASED', 'SUCCEEDED', 'RETRYABLE_FAILURE',
        'TERMINAL_FAILURE', 'INTERACTION_REQUIRED',
        'SKIPPED_ALREADY_HAVE_CONTENT', 'SKIPPED_POLICY'
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
    UNIQUE(artifact_id, requested_url)
);

CREATE INDEX IF NOT EXISTS idx_credit_rating_queue_claim
ON credit_rating_download_queue(status, next_attempt_at_utc, priority, download_job_id);

CREATE TABLE IF NOT EXISTS credit_rating_resolution_attempts (
    resolution_attempt_id INTEGER PRIMARY KEY,
    download_job_id INTEGER NOT NULL
      REFERENCES credit_rating_download_queue(download_job_id),
    artifact_id INTEGER NOT NULL REFERENCES document_artifacts(artifact_id),
    original_url TEXT NOT NULL,
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT,
    resolution_status TEXT NOT NULL,
    resolver_version TEXT NOT NULL,
    final_url TEXT,
    error_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS credit_rating_resolution_steps (
    resolution_attempt_id INTEGER NOT NULL
      REFERENCES credit_rating_resolution_attempts(resolution_attempt_id) ON DELETE CASCADE,
    step_position INTEGER NOT NULL,
    step_type TEXT NOT NULL,
    requested_url TEXT NOT NULL,
    final_url TEXT,
    request_method TEXT NOT NULL,
    http_status INTEGER,
    declared_media_type TEXT,
    detected_media_type TEXT,
    blob_id INTEGER REFERENCES response_blobs(blob_id),
    selected INTEGER NOT NULL DEFAULT 0 CHECK (selected IN (0, 1)),
    evidence_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(resolution_attempt_id, step_position)
);

CREATE INDEX IF NOT EXISTS idx_credit_rating_resolution_job
ON credit_rating_resolution_attempts(download_job_id, resolution_attempt_id);
