-- Durable final-state ledger for the frozen Document Acquisition corpus.
-- This records acquisition closure only; it carries no research or ML labels.

CREATE TABLE document_acquisition_closure_states (
    scope_id TEXT NOT NULL REFERENCES artifact_download_scopes(scope_id),
    artifact_id INTEGER NOT NULL REFERENCES document_artifacts(artifact_id),
    final_state TEXT NOT NULL CHECK (final_state IN (
      'ACQUIRED_VALIDATED',
      'ACQUIRED_ALTERNATE_SOURCE',
      'ACQUIRED_PARTIAL_EVIDENCE',
      'NOT_OFFERED_CONFIRMED',
      'SOURCE_MISSING_TERMINAL',
      'POLICY_BLOCKED_NO_ALTERNATE',
      'LICENSE_OR_MANUAL_REQUIRED',
      'TECHNICAL_FAILURE_RETRYABLE',
      'TECHNICAL_FAILURE_EXHAUSTED',
      'IDENTITY_AMBIGUOUS',
      'PERIOD_AMBIGUOUS',
      'QUARANTINED_INVALID_SOURCE'
    )),
    evidence_json TEXT NOT NULL,
    evaluated_at_utc TEXT NOT NULL,
    PRIMARY KEY (scope_id, artifact_id)
);

CREATE INDEX idx_document_acquisition_closure_state
ON document_acquisition_closure_states(scope_id, final_state, artifact_id);
