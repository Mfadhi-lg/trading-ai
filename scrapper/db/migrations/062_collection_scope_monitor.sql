-- Keep eligibility separate from immutable financial/document history.
CREATE TABLE screener_collection_scope (
    company_path TEXT PRIMARY KEY,
    company_id INTEGER REFERENCES companies(company_id),
    eligibility TEXT NOT NULL CHECK (eligibility IN ('eligible','below_floor','unknown')),
    market_cap_crore TEXT,
    floor_crore TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    next_check_at_utc TEXT NOT NULL,
    claim_token TEXT,
    claim_expires_at_utc TEXT,
    check_failures INTEGER NOT NULL DEFAULT 0,
    last_check_error TEXT
);
CREATE INDEX idx_collection_scope_due ON screener_collection_scope(next_check_at_utc);
CREATE INDEX idx_collection_scope_company ON screener_collection_scope(company_id,eligibility);
CREATE TABLE screener_scope_aliases (
    company_path TEXT PRIMARY KEY,
    scope_path TEXT NOT NULL REFERENCES screener_collection_scope(company_path),
    identity_basis TEXT NOT NULL
);
ALTER TABLE screener_company_queue ADD COLUMN collection_disposition TEXT;
-- The existing terminal status remains as historical evidence. Operator
-- reports expose exclusions separately, and workers must not claim them.
CREATE TABLE screener_scope_observations (
    observation_id INTEGER PRIMARY KEY,
    company_path TEXT NOT NULL REFERENCES screener_collection_scope(company_path),
    observed_at_utc TEXT NOT NULL,
    eligibility TEXT NOT NULL,
    market_cap_crore TEXT,
    evidence_json TEXT NOT NULL
);
