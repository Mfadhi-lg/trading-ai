CREATE TABLE artifact_period_corrections (
    correction_id INTEGER PRIMARY KEY,
    artifact_id INTEGER NOT NULL REFERENCES document_artifacts(artifact_id),
    old_period_end TEXT NOT NULL,
    new_period_end TEXT,
    reason TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    corrected_at_utc TEXT NOT NULL,
    UNIQUE(artifact_id,old_period_end,reason)
);
