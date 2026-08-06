-- M04 versioned scheme registry and deterministic workbook acceptance.
ALTER TABLE mf_holdings ADD COLUMN scheme_id INTEGER REFERENCES mf_schemes(scheme_id);
ALTER TABLE mf_holdings ADD COLUMN scheme_category TEXT;

CREATE INDEX IF NOT EXISTS ix_mf_holdings_scheme_identity
ON mf_holdings(scheme_id, disclosure_month, import_file_id);

CREATE TABLE IF NOT EXISTS mf_scheme_name_versions (
    scheme_name_version_id INTEGER PRIMARY KEY,
    scheme_id INTEGER NOT NULL REFERENCES mf_schemes(scheme_id),
    scheme_name_raw TEXT NOT NULL,
    valid_from_month TEXT NOT NULL,
    valid_to_month TEXT,
    naming_source TEXT NOT NULL,
    naming_rule_version TEXT NOT NULL,
    first_seen_import_file_id INTEGER NOT NULL
        REFERENCES mf_import_files(import_file_id),
    created_at_utc TEXT NOT NULL,
    CHECK (valid_to_month IS NULL OR valid_to_month >= valid_from_month),
    UNIQUE(scheme_id, scheme_name_raw, valid_from_month, naming_rule_version)
);

CREATE TABLE IF NOT EXISTS mf_scheme_classifications (
    scheme_classification_id INTEGER PRIMARY KEY,
    scheme_id INTEGER NOT NULL REFERENCES mf_schemes(scheme_id),
    scheme_management_type TEXT NOT NULL CHECK (
      scheme_management_type IN (
        'active', 'index', 'ETF', 'arbitrage', 'fund_of_funds', 'unknown'
      )
    ),
    is_equity_scheme INTEGER NOT NULL CHECK (is_equity_scheme IN (0, 1)),
    scheme_category TEXT NOT NULL,
    valid_from_month TEXT NOT NULL,
    valid_to_month TEXT,
    classification_source TEXT NOT NULL,
    classification_rule_version TEXT NOT NULL,
    first_seen_import_file_id INTEGER NOT NULL
        REFERENCES mf_import_files(import_file_id),
    created_at_utc TEXT NOT NULL,
    CHECK (valid_to_month IS NULL OR valid_to_month >= valid_from_month),
    UNIQUE(
      scheme_id, valid_from_month, classification_rule_version,
      scheme_management_type, is_equity_scheme, scheme_category
    )
);

CREATE INDEX IF NOT EXISTS ix_mf_scheme_classifications_period
ON mf_scheme_classifications(scheme_id, valid_from_month, valid_to_month);

CREATE TABLE IF NOT EXISTS mf_workbook_acceptance_events (
    workbook_acceptance_event_id INTEGER PRIMARY KEY,
    import_file_id INTEGER NOT NULL UNIQUE REFERENCES mf_import_files(import_file_id),
    decided_at_utc TEXT NOT NULL,
    decided_by TEXT NOT NULL,
    acceptance_mode TEXT NOT NULL,
    validation_status TEXT NOT NULL CHECK (
      validation_status IN ('passed', 'passed_with_warnings')
    ),
    scheme_count INTEGER NOT NULL CHECK (scheme_count > 0),
    previous_import_file_ids_json TEXT NOT NULL,
    decision_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mf_accepted_version_events (
    accepted_version_event_id INTEGER PRIMARY KEY,
    publisher TEXT NOT NULL,
    scheme_id INTEGER NOT NULL REFERENCES mf_schemes(scheme_id),
    disclosure_month TEXT NOT NULL,
    import_file_id INTEGER NOT NULL REFERENCES mf_import_files(import_file_id),
    previous_import_file_id INTEGER REFERENCES mf_import_files(import_file_id),
    scheme_classification_id INTEGER NOT NULL
        REFERENCES mf_scheme_classifications(scheme_classification_id),
    accepted_at_utc TEXT NOT NULL,
    accepted_by TEXT NOT NULL,
    acceptance_mode TEXT NOT NULL,
    UNIQUE(publisher, scheme_id, disclosure_month, import_file_id)
);

CREATE INDEX IF NOT EXISTS ix_mf_accepted_version_events_history
ON mf_accepted_version_events(publisher, scheme_id, disclosure_month, accepted_at_utc);

-- Accepted query surfaces follow the current per-scheme pointer. Historical
-- imports retain accepted state and events, but cannot leak through these views
-- after a newer version supersedes their pointer.
CREATE VIEW IF NOT EXISTS mf_accepted_holdings AS
SELECT h.*,
       c.scheme_management_type,
       c.is_equity_scheme,
       c.scheme_category AS registry_scheme_category,
       c.classification_source,
       c.classification_rule_version,
       c.scheme_classification_id
FROM mf_holdings AS h
JOIN mf_accepted_versions AS av
  ON av.publisher=h.publisher
 AND av.scheme_id=h.scheme_id
 AND av.disclosure_month=h.disclosure_month
 AND av.import_file_id=h.import_file_id
JOIN mf_scheme_classifications AS c
  ON c.scheme_classification_id=(
    SELECT MAX(c2.scheme_classification_id)
    FROM mf_scheme_classifications AS c2
    WHERE c2.scheme_id=h.scheme_id
      AND c2.valid_from_month<=h.disclosure_month
      AND (c2.valid_to_month IS NULL OR c2.valid_to_month>=h.disclosure_month)
  );

CREATE VIEW IF NOT EXISTS mf_all_scheme_accepted_holdings AS
SELECT * FROM mf_accepted_holdings;

CREATE VIEW IF NOT EXISTS mf_active_equity_accepted_holdings AS
SELECT *
FROM mf_accepted_holdings
WHERE scheme_management_type='active' AND is_equity_scheme=1;

DROP VIEW IF EXISTS mf_stock_feature_eligible_holdings;
CREATE VIEW mf_stock_feature_eligible_holdings AS
SELECT *
FROM mf_accepted_holdings
WHERE source_table_kind='primary_portfolio'
  AND security_id IS NOT NULL
  AND security_match_status='matched';

CREATE VIEW IF NOT EXISTS mf_active_equity_stock_feature_eligible_holdings AS
SELECT *
FROM mf_active_equity_accepted_holdings
WHERE source_table_kind='primary_portfolio'
  AND security_id IS NOT NULL
  AND security_match_status='matched';
