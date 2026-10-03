-- DA-06: official alternate-source recovery for policy-blocked rating artifacts.
--
-- This is a discovery/matching registry only.  Candidate bytes are never
-- stored here; accepted URLs are attached to the existing artifact and enter
-- the migration-042 D02 queue.

ALTER TABLE artifact_sources ADD COLUMN automation_status TEXT NOT NULL
  DEFAULT 'AUTOMATION_ELIGIBILITY_UNKNOWN' CHECK (automation_status IN (
    'AVAILABLE', 'BLOCKED_TERMS', 'INTERACTION_REQUIRED',
    'AUTOMATION_ELIGIBILITY_UNKNOWN'
  ));

ALTER TABLE artifact_sources ADD COLUMN document_originator TEXT CHECK (
  document_originator IS NULL OR document_originator IN ('CRISIL', 'ICRA')
);
ALTER TABLE artifact_sources ADD COLUMN retrieval_source_type TEXT;
ALTER TABLE artifact_sources ADD COLUMN retrieval_host TEXT;
ALTER TABLE artifact_sources ADD COLUMN published_at_utc TEXT;
ALTER TABLE artifact_sources ADD COLUMN available_at_utc TEXT;
ALTER TABLE artifact_sources ADD COLUMN availability_basis TEXT;
ALTER TABLE artifact_sources ADD COLUMN recovery_target_id INTEGER
  REFERENCES artifact_recovery_targets(recovery_target_id);
ALTER TABLE artifact_sources ADD COLUMN recovery_candidate_id INTEGER
  REFERENCES artifact_recovery_candidates(candidate_id);
ALTER TABLE artifact_sources ADD COLUMN matching_confidence TEXT CHECK (
  matching_confidence IS NULL OR matching_confidence IN (
    'EXACT', 'HIGH', 'AMBIGUOUS', 'NO_MATCH',
    'REJECTED_POLICY', 'REJECTED_INVALID'
  )
);
ALTER TABLE artifact_sources ADD COLUMN recovery_evidence_json TEXT NOT NULL
  DEFAULT '{}';

-- Make the original automation decision explicit on the source without
-- changing its economic availability.  Queue/claim/network policy remains the
-- authoritative enforcement boundary.
UPDATE artifact_sources
   SET automation_status='BLOCKED_TERMS',
       document_originator=CASE
         WHEN lower(document_url) LIKE '%://crisil.com/%'
           OR lower(document_url) LIKE '%://%.crisil.com/%'
           OR lower(document_url) LIKE '%://crisilratings.com/%'
           OR lower(document_url) LIKE '%://%.crisilratings.com/%'
           THEN 'CRISIL'
         WHEN lower(document_url) LIKE '%://icra.in/%'
           OR lower(document_url) LIKE '%://%.icra.in/%'
           THEN 'ICRA'
       END
 WHERE lower(document_url) LIKE '%://crisil.com/%'
    OR lower(document_url) LIKE '%://%.crisil.com/%'
    OR lower(document_url) LIKE '%://crisilratings.com/%'
    OR lower(document_url) LIKE '%://%.crisilratings.com/%'
    OR lower(document_url) LIKE '%://icra.in/%'
    OR lower(document_url) LIKE '%://%.icra.in/%';

CREATE TABLE artifact_recovery_targets (
    recovery_target_id INTEGER PRIMARY KEY,
    artifact_id INTEGER NOT NULL UNIQUE REFERENCES document_artifacts(artifact_id),
    company_id INTEGER NOT NULL REFERENCES companies(company_id),
    artifact_type TEXT NOT NULL CHECK (artifact_type='credit_rating'),
    document_originator TEXT NOT NULL CHECK (document_originator IN ('CRISIL', 'ICRA')),
    blocked_source_id INTEGER NOT NULL REFERENCES artifact_sources(artifact_source_id),
    blocked_source_url TEXT NOT NULL,
    blocked_source_host TEXT NOT NULL,
    company_canonical_name TEXT NOT NULL,
    company_aliases_json TEXT NOT NULL DEFAULT '[]',
    nse_symbols_json TEXT NOT NULL DEFAULT '[]',
    bse_codes_json TEXT NOT NULL DEFAULT '[]',
    isins_json TEXT NOT NULL DEFAULT '[]',
    event_date TEXT,
    fiscal_period TEXT,
    rating TEXT,
    outlook TEXT,
    rating_action TEXT,
    instrument_or_facility TEXT,
    amount TEXT,
    amount_unit TEXT,
    source_confidence TEXT,
    field_provenance_json TEXT NOT NULL DEFAULT '{}',
    contract_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
      'PENDING', 'SEARCHING', 'CANDIDATE_FOUND', 'RECOVERED',
      'ALREADY_SATISFIED', 'AMBIGUOUS', 'NO_OFFICIAL_SOURCE_FOUND',
      'RETRYABLE_FAILURE', 'TERMINAL_FAILURE', 'LICENSE_OR_MANUAL_REQUIRED'
    )),
    full_rationale_coverage INTEGER NOT NULL DEFAULT 0 CHECK (full_rationale_coverage IN (0,1)),
    basic_rating_coverage INTEGER NOT NULL DEFAULT 0 CHECK (basic_rating_coverage IN (0,1)),
    resolved_candidate_id INTEGER,
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_attempt_at_utc TEXT,
    lease_owner TEXT,
    lease_expires_at_utc TEXT,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    CHECK (
      (lease_owner IS NULL AND lease_expires_at_utc IS NULL)
      OR (lease_owner IS NOT NULL AND lease_expires_at_utc IS NOT NULL)
    )
);

CREATE INDEX idx_artifact_recovery_targets_claim
ON artifact_recovery_targets(status, next_attempt_at_utc, lease_expires_at_utc,
                             recovery_target_id);
CREATE INDEX idx_artifact_recovery_targets_originator
ON artifact_recovery_targets(document_originator, status);

CREATE TABLE artifact_recovery_quarantine (
    artifact_id INTEGER PRIMARY KEY REFERENCES document_artifacts(artifact_id),
    reason_code TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    quarantined_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE artifact_recovery_attempts (
    recovery_attempt_id INTEGER PRIMARY KEY,
    recovery_target_id INTEGER NOT NULL
      REFERENCES artifact_recovery_targets(recovery_target_id),
    source_type TEXT NOT NULL,
    attempt_number INTEGER NOT NULL CHECK (attempt_number >= 1),
    status TEXT NOT NULL CHECK (status IN (
      'SEARCHING', 'CANDIDATE_FOUND', 'AMBIGUOUS', 'NO_OFFICIAL_SOURCE_FOUND',
      'RETRYABLE_FAILURE', 'TERMINAL_FAILURE', 'INTERACTION_REQUIRED'
    )),
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT,
    request_evidence_json TEXT NOT NULL DEFAULT '{}',
    result_evidence_json TEXT NOT NULL DEFAULT '{}',
    error_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(recovery_target_id, source_type, attempt_number)
);

CREATE INDEX idx_artifact_recovery_attempts_target
ON artifact_recovery_attempts(recovery_target_id, recovery_attempt_id);

CREATE TABLE artifact_recovery_candidates (
    candidate_id INTEGER PRIMARY KEY,
    recovery_target_id INTEGER NOT NULL
      REFERENCES artifact_recovery_targets(recovery_target_id),
    artifact_id INTEGER NOT NULL REFERENCES document_artifacts(artifact_id),
    candidate_url TEXT NOT NULL,
    retrieval_host TEXT NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN (
      'NSE_EXCHANGE_ATTACHMENT', 'BSE_EXCHANGE_ATTACHMENT',
      'NSE_DEBT_DOCUMENT', 'BSE_DEBT_DOCUMENT',
      'ISSUER_IR_ATTACHMENT', 'REGULATORY_METADATA'
    )),
    discovered_at_utc TEXT NOT NULL,
    discovery_page_url TEXT,
    discovery_blob_id INTEGER REFERENCES response_blobs(blob_id),
    document_title TEXT,
    publication_date TEXT,
    published_at_utc TEXT,
    available_at_utc TEXT NOT NULL,
    availability_basis TEXT NOT NULL,
    company_identifiers_json TEXT NOT NULL DEFAULT '{}',
    document_originator TEXT CHECK (
      document_originator IS NULL OR document_originator IN ('CRISIL', 'ICRA')
    ),
    rating TEXT,
    outlook TEXT,
    instrument_or_facility TEXT,
    source_evidence_json TEXT NOT NULL DEFAULT '{}',
    matching_features_json TEXT NOT NULL DEFAULT '{}',
    confidence TEXT NOT NULL CHECK (confidence IN (
      'EXACT', 'HIGH', 'AMBIGUOUS', 'NO_MATCH',
      'REJECTED_POLICY', 'REJECTED_INVALID'
    )),
    rejection_reason TEXT,
    content_class TEXT NOT NULL CHECK (content_class IN (
      'FULL_RATING_RATIONALE', 'RATING_RATIONALE_EXCERPT', 'RATING_LETTER',
      'RATING_ACTION_INTIMATION', 'ISSUE_DOCUMENT_EMBEDDED_RATIONALE',
      'ANNUAL_REPORT_RATING_SUMMARY', 'REGULATORY_RATING_METADATA',
      'UNKNOWN_RATING_DOCUMENT'
    )),
    contract_version TEXT NOT NULL,
    attached_source_id INTEGER REFERENCES artifact_sources(artifact_source_id),
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    UNIQUE(recovery_target_id, source_type, candidate_url)
);

CREATE INDEX idx_artifact_recovery_candidates_decision
ON artifact_recovery_candidates(recovery_target_id, confidence, candidate_id);

CREATE TABLE artifact_source_evidence_classifications (
    classification_id INTEGER PRIMARY KEY,
    artifact_source_id INTEGER NOT NULL REFERENCES artifact_sources(artifact_source_id),
    content_id INTEGER REFERENCES artifact_contents(content_id),
    content_class TEXT NOT NULL CHECK (content_class IN (
      'FULL_RATING_RATIONALE', 'RATING_RATIONALE_EXCERPT', 'RATING_LETTER',
      'RATING_ACTION_INTIMATION', 'ISSUE_DOCUMENT_EMBEDDED_RATIONALE',
      'ANNUAL_REPORT_RATING_SUMMARY', 'REGULATORY_RATING_METADATA',
      'UNKNOWN_RATING_DOCUMENT'
    )),
    classification_basis_json TEXT NOT NULL,
    classifier_name TEXT NOT NULL,
    classifier_version TEXT NOT NULL,
    classified_at_utc TEXT NOT NULL,
    UNIQUE(artifact_source_id, content_id, classifier_name, classifier_version)
);

CREATE INDEX idx_artifact_source_evidence_class
ON artifact_source_evidence_classifications(content_class, artifact_source_id);

