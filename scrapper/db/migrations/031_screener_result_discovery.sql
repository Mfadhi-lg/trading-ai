-- Authenticated Screener result discovery, immutable evidence and lease-safe queue.

CREATE TABLE IF NOT EXISTS screener_screen_runs (
    screen_run_id INTEGER PRIMARY KEY,
    query_role TEXT NOT NULL,
    query_raw TEXT NOT NULL,
    requested_url TEXT NOT NULL,
    fetch_id INTEGER NOT NULL REFERENCES fetch_observations(fetch_id),
    blob_id INTEGER NOT NULL REFERENCES response_blobs(blob_id),
    parser_name TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL,
    result_count INTEGER NOT NULL CHECK (result_count >= 0),
    page_number INTEGER NOT NULL CHECK (page_number >= 1),
    page_count INTEGER NOT NULL CHECK (page_count >= 1),
    captured_row_count INTEGER NOT NULL CHECK (captured_row_count >= 0),
    coverage_status TEXT NOT NULL CHECK (coverage_status IN ('complete', 'partial', 'rejected')),
    validation_status TEXT NOT NULL CHECK (validation_status IN ('passed', 'failed')),
    UNIQUE(blob_id, parser_name, parser_version, query_raw)
);

CREATE TABLE IF NOT EXISTS screener_screen_source_cells (
    screen_run_id INTEGER NOT NULL REFERENCES screener_screen_runs(screen_run_id) ON DELETE CASCADE,
    row_position INTEGER NOT NULL,
    column_position INTEGER NOT NULL,
    row_label_raw TEXT,
    column_label_raw TEXT,
    raw_text TEXT NOT NULL,
    normalized_display_text TEXT NOT NULL,
    link_url TEXT,
    cell_role TEXT NOT NULL,
    PRIMARY KEY(screen_run_id, row_position, column_position)
);

CREATE TABLE IF NOT EXISTS screener_result_candidates (
    candidate_id INTEGER PRIMARY KEY,
    screen_run_id INTEGER NOT NULL REFERENCES screener_screen_runs(screen_run_id) ON DELETE CASCADE,
    source_position INTEGER NOT NULL,
    screen_row_company_id TEXT NOT NULL,
    company_name_raw TEXT NOT NULL,
    company_href_raw TEXT NOT NULL,
    company_path_normalized TEXT NOT NULL,
    reporting_scope_hint TEXT NOT NULL CHECK (reporting_scope_hint IN ('consolidated', 'standalone')),
    last_result_date_display TEXT NOT NULL,
    result_period TEXT NOT NULL,
    discovered_at_utc TEXT NOT NULL,
    UNIQUE(screen_run_id, source_position)
);

CREATE TABLE IF NOT EXISTS screener_company_collection_state (
    company_path_normalized TEXT NOT NULL,
    reporting_scope TEXT NOT NULL CHECK (reporting_scope IN ('consolidated', 'standalone')),
    company_id INTEGER REFERENCES companies(company_id),
    latest_discovered_result_period TEXT NOT NULL,
    latest_accepted_result_period TEXT,
    latest_accepted_parse_id INTEGER REFERENCES parse_artifacts(parse_id),
    updated_at_utc TEXT NOT NULL,
    PRIMARY KEY(company_path_normalized, reporting_scope)
);

CREATE TABLE IF NOT EXISTS screener_company_queue (
    queue_item_id INTEGER PRIMARY KEY,
    candidate_id INTEGER NOT NULL REFERENCES screener_result_candidates(candidate_id),
    company_path_normalized TEXT NOT NULL,
    reporting_scope TEXT NOT NULL CHECK (reporting_scope IN ('consolidated', 'standalone')),
    result_period TEXT NOT NULL,
    queue_reason TEXT NOT NULL CHECK (queue_reason IN ('new_company', 'result_period_advanced')),
    status TEXT NOT NULL CHECK (status IN (
      'pending', 'claimed', 'running', 'accepted', 'failed_retryable',
      'failed_terminal', 'manual_review', 'skipped_already_accepted'
    )),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_attempt_at_utc TEXT,
    claim_token TEXT UNIQUE,
    claimed_at_utc TEXT,
    claim_expires_at_utc TEXT,
    accepted_parse_id INTEGER REFERENCES parse_artifacts(parse_id),
    accepted_export_path TEXT,
    last_error_json TEXT NOT NULL DEFAULT '{}',
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    UNIQUE(company_path_normalized, reporting_scope, result_period),
    CHECK (
      (claim_token IS NULL AND claimed_at_utc IS NULL AND claim_expires_at_utc IS NULL)
      OR (claim_token IS NOT NULL AND claimed_at_utc IS NOT NULL AND claim_expires_at_utc IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS screener_company_queue_events (
    queue_event_id INTEGER PRIMARY KEY,
    queue_item_id INTEGER NOT NULL REFERENCES screener_company_queue(queue_item_id),
    event_at_utc TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    worker_id TEXT,
    evidence_json TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_screener_queue_due
ON screener_company_queue(status, next_attempt_at_utc, claim_expires_at_utc, queue_item_id);

CREATE INDEX IF NOT EXISTS idx_screener_candidates_period
ON screener_result_candidates(result_period, reporting_scope_hint);

CREATE INDEX IF NOT EXISTS idx_screener_screen_runs_role
ON screener_screen_runs(query_role, observed_at_utc);
