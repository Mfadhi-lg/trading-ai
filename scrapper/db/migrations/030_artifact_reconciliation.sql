-- D09 durable delayed-artifact expectations, lease-safe claims and append-only checks.

CREATE TABLE IF NOT EXISTS artifact_events (
    event_id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(company_id),
    event_key TEXT NOT NULL,
    event_type TEXT NOT NULL DEFAULT 'earnings_call' CHECK (
      event_type IN ('earnings_call', 'results', 'rating_review', 'other')
    ),
    event_date TEXT,
    period_end TEXT,
    fiscal_year INTEGER,
    fiscal_quarter INTEGER CHECK (
      fiscal_quarter IS NULL OR fiscal_quarter BETWEEN 1 AND 4
    ),
    evidenced_at_utc TEXT NOT NULL,
    evidence_source TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(company_id, event_key)
);

CREATE TABLE IF NOT EXISTS artifact_expectations (
    expectation_id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(company_id),
    artifact_type TEXT NOT NULL CHECK (
      artifact_type IN ('presentation', 'credit_rating', 'transcript', 'recording')
    ),
    event_id INTEGER NOT NULL REFERENCES artifact_events(event_id),
    expectation_basis TEXT NOT NULL,
    expected_after_utc TEXT NOT NULL,
    availability_status TEXT NOT NULL DEFAULT 'expected_not_yet_published' CHECK (
      availability_status IN (
        'available', 'expected_not_yet_published', 'not_offered',
        'not_applicable', 'no_rows_reported', 'source_not_covered',
        'inaccessible', 'discovery_failed', 'unknown'
      )
    ),
    first_checked_at_utc TEXT,
    last_checked_at_utc TEXT,
    next_check_at_utc TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    terminal_reason TEXT,
    last_error_json TEXT NOT NULL DEFAULT '{}',
    schedule_json TEXT NOT NULL,
    claim_token TEXT UNIQUE,
    claimed_at_utc TEXT,
    claim_expires_at_utc TEXT,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    UNIQUE(company_id, artifact_type, event_id),
    CHECK (
      (claim_token IS NULL AND claimed_at_utc IS NULL AND claim_expires_at_utc IS NULL)
      OR
      (claim_token IS NOT NULL AND claimed_at_utc IS NOT NULL
       AND claim_expires_at_utc IS NOT NULL)
    )
);

CREATE TABLE IF NOT EXISTS artifact_check_events (
    check_id INTEGER PRIMARY KEY,
    expectation_id INTEGER NOT NULL REFERENCES artifact_expectations(expectation_id),
    checked_at_utc TEXT NOT NULL,
    source TEXT NOT NULL,
    requested_url TEXT,
    result_status TEXT NOT NULL CHECK (
      result_status IN (
        'available', 'expected_not_yet_published', 'not_offered',
        'not_applicable', 'no_rows_reported', 'source_not_covered',
        'inaccessible', 'discovery_failed', 'unknown'
      )
    ),
    evidence_json TEXT NOT NULL DEFAULT '{}',
    discovered_artifact_id INTEGER REFERENCES document_artifacts(artifact_id),
    error_json TEXT NOT NULL DEFAULT '{}',
    retryable INTEGER NOT NULL CHECK (retryable IN (0, 1)),
    next_check_at_utc TEXT,
    claim_token TEXT NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_artifact_expectations_due
ON artifact_expectations(next_check_at_utc, claim_expires_at_utc, expectation_id);

CREATE INDEX IF NOT EXISTS idx_artifact_expectations_coverage
ON artifact_expectations(company_id, event_id, artifact_type);

CREATE INDEX IF NOT EXISTS idx_artifact_check_events_expectation
ON artifact_check_events(expectation_id, check_id);

CREATE TRIGGER IF NOT EXISTS artifact_expectation_event_company_insert_guard
BEFORE INSERT ON artifact_expectations
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM artifact_events
    WHERE event_id=NEW.event_id AND company_id=NEW.company_id
  ) THEN RAISE(ABORT, 'artifact expectation event company mismatch') END;
END;

CREATE TRIGGER IF NOT EXISTS artifact_expectation_event_company_update_guard
BEFORE UPDATE OF company_id, event_id ON artifact_expectations
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM artifact_events
    WHERE event_id=NEW.event_id AND company_id=NEW.company_id
  ) THEN RAISE(ABORT, 'artifact expectation event company mismatch') END;
END;

CREATE TRIGGER IF NOT EXISTS artifact_check_discovery_identity_guard
BEFORE INSERT ON artifact_check_events
WHEN NEW.discovered_artifact_id IS NOT NULL
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1
    FROM artifact_expectations AS expectation
    JOIN artifact_events AS event USING (event_id)
    JOIN document_artifacts AS artifact
      ON artifact.artifact_id=NEW.discovered_artifact_id
    WHERE expectation.expectation_id=NEW.expectation_id
      AND artifact.company_id=expectation.company_id
      AND artifact.artifact_type=expectation.artifact_type
      AND (
        (event.event_date IS NOT NULL AND artifact.event_date=event.event_date)
        OR (event.period_end IS NOT NULL AND artifact.period_end=event.period_end)
        OR (event.fiscal_year IS NOT NULL AND event.fiscal_quarter IS NOT NULL
            AND artifact.fiscal_year=event.fiscal_year
            AND artifact.fiscal_quarter=event.fiscal_quarter)
      )
  ) THEN RAISE(ABORT, 'discovered artifact does not match expectation') END;
END;

CREATE VIEW IF NOT EXISTS artifact_retry_coverage AS
SELECT expectation.*, event.event_key, event.event_type, event.event_date,
       event.period_end, event.fiscal_year, event.fiscal_quarter,
       company.current_legal_name
FROM artifact_expectations AS expectation
JOIN artifact_events AS event USING (event_id)
JOIN companies AS company USING (company_id);
