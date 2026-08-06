-- M06 exact stock-level monthly features derived from current M05 change rows.
-- Decimal aggregates are materialized by the Python Decimal engine; these views
-- only select pointer-safe versions and never ask SQLite to sum decimal text.
CREATE TABLE IF NOT EXISTS mf_stock_month_feature_sets (
    feature_set_id INTEGER PRIMARY KEY,
    publisher TEXT NOT NULL,
    previous_month TEXT NOT NULL,
    current_month TEXT NOT NULL,
    aggregation_status TEXT NOT NULL CHECK (
      aggregation_status IN ('generated', 'skipped')
    ),
    reason_code TEXT NOT NULL,
    completeness_evidence_json TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL UNIQUE,
    generated_at_utc TEXT NOT NULL,
    CHECK (previous_month < current_month)
);

CREATE INDEX IF NOT EXISTS ix_mf_stock_feature_sets_pair
ON mf_stock_month_feature_sets(
  publisher, previous_month, current_month, feature_set_id
);

CREATE TABLE IF NOT EXISTS mf_stock_month_feature_inputs (
    feature_set_id INTEGER NOT NULL
        REFERENCES mf_stock_month_feature_sets(feature_set_id),
    change_set_id INTEGER NOT NULL
        REFERENCES mf_scheme_month_change_sets(change_set_id),
    scheme_id INTEGER NOT NULL REFERENCES mf_schemes(scheme_id),
    scheme_classification_id INTEGER NOT NULL
        REFERENCES mf_scheme_classifications(scheme_classification_id),
    previous_import_file_id INTEGER NOT NULL
        REFERENCES mf_import_files(import_file_id),
    current_import_file_id INTEGER NOT NULL
        REFERENCES mf_import_files(import_file_id),
    previous_content_sha256 TEXT NOT NULL,
    current_content_sha256 TEXT NOT NULL,
    PRIMARY KEY(feature_set_id, change_set_id),
    UNIQUE(feature_set_id, scheme_id)
);

CREATE TABLE IF NOT EXISTS mf_stock_month_feature_validations (
    feature_set_id INTEGER NOT NULL
        REFERENCES mf_stock_month_feature_sets(feature_set_id),
    rule_code TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('error', 'warning')),
    validation_status TEXT NOT NULL CHECK (
      validation_status IN ('passed', 'failed', 'not_evaluated')
    ),
    details_json TEXT NOT NULL,
    PRIMARY KEY(feature_set_id, rule_code)
);

CREATE TABLE IF NOT EXISTS mf_stock_monthly_features (
    stock_month_feature_id INTEGER PRIMARY KEY,
    feature_set_id INTEGER NOT NULL
        REFERENCES mf_stock_month_feature_sets(feature_set_id),
    publisher TEXT NOT NULL,
    security_id INTEGER NOT NULL REFERENCES mf_securities(security_id),
    isin_normalized TEXT NOT NULL,
    disclosure_month TEXT NOT NULL,
    feature_scope TEXT NOT NULL CHECK (
      feature_scope IN ('all_scheme', 'active_equity')
    ),
    active_fund_holder_count INTEGER NOT NULL CHECK (active_fund_holder_count >= 0),
    new_fund_entries INTEGER NOT NULL CHECK (new_fund_entries >= 0),
    complete_fund_exits INTEGER NOT NULL CHECK (complete_fund_exits >= 0),
    funds_increasing_quantity INTEGER NOT NULL CHECK (funds_increasing_quantity >= 0),
    funds_decreasing_quantity INTEGER NOT NULL CHECK (funds_decreasing_quantity >= 0),
    funds_unchanged INTEGER NOT NULL CHECK (funds_unchanged >= 0),
    gross_quantity_bought TEXT NOT NULL,
    gross_quantity_sold TEXT NOT NULL,
    net_quantity_change TEXT NOT NULL,
    total_portfolio_weight TEXT NOT NULL,
    portfolio_weight_change TEXT NOT NULL,
    active_equity_holder_count INTEGER NOT NULL CHECK (active_equity_holder_count >= 0),
    passive_holder_count INTEGER NOT NULL CHECK (passive_holder_count >= 0),
    arbitrage_holder_count INTEGER NOT NULL CHECK (arbitrage_holder_count >= 0),
    accumulation_breadth INTEGER NOT NULL CHECK (accumulation_breadth >= 0),
    distribution_breadth INTEGER NOT NULL CHECK (distribution_breadth >= 0),
    active_fund_accumulation_breadth INTEGER NOT NULL CHECK (
      active_fund_accumulation_breadth >= 0
    ),
    generated_at_utc TEXT NOT NULL,
    UNIQUE(feature_set_id, security_id, feature_scope)
);

CREATE INDEX IF NOT EXISTS ix_mf_stock_monthly_features_lookup
ON mf_stock_monthly_features(
  publisher, disclosure_month, feature_scope, isin_normalized
);

CREATE TABLE IF NOT EXISTS mf_stock_month_feature_lineage (
    stock_month_feature_id INTEGER NOT NULL
        REFERENCES mf_stock_monthly_features(stock_month_feature_id),
    scheme_security_change_id INTEGER NOT NULL
        REFERENCES mf_scheme_security_month_changes(scheme_security_change_id),
    scheme_classification_id INTEGER NOT NULL
        REFERENCES mf_scheme_classifications(scheme_classification_id),
    previous_content_sha256 TEXT NOT NULL,
    current_content_sha256 TEXT NOT NULL,
    PRIMARY KEY(stock_month_feature_id, scheme_security_change_id)
);

-- A generated feature set is current only while it includes exactly every
-- current M05 set for the pair and each effective scheme classification remains
-- the same version used for aggregation. Superseded features stay historical.
CREATE VIEW IF NOT EXISTS mf_current_stock_month_feature_sets AS
SELECT fs.*
FROM mf_stock_month_feature_sets AS fs
WHERE fs.aggregation_status='generated'
  AND EXISTS (
    SELECT 1 FROM mf_stock_month_feature_inputs AS input
    WHERE input.feature_set_id=fs.feature_set_id
  )
  AND NOT EXISTS (
    SELECT 1
    FROM mf_stock_month_feature_inputs AS input
    LEFT JOIN mf_current_scheme_month_change_sets AS current_set
      ON current_set.change_set_id=input.change_set_id
    WHERE input.feature_set_id=fs.feature_set_id
      AND current_set.change_set_id IS NULL
  )
  AND NOT EXISTS (
    SELECT 1
    FROM mf_current_scheme_month_change_sets AS current_set
    LEFT JOIN mf_stock_month_feature_inputs AS input
      ON input.feature_set_id=fs.feature_set_id
     AND input.change_set_id=current_set.change_set_id
    WHERE current_set.publisher=fs.publisher
      AND current_set.previous_month=fs.previous_month
      AND current_set.current_month=fs.current_month
      AND input.change_set_id IS NULL
  )
  AND NOT EXISTS (
    SELECT 1
    FROM mf_stock_month_feature_inputs AS input
    WHERE input.feature_set_id=fs.feature_set_id
      AND input.scheme_classification_id != (
        SELECT MAX(classification.scheme_classification_id)
        FROM mf_scheme_classifications AS classification
        WHERE classification.scheme_id=input.scheme_id
          AND classification.valid_from_month<=fs.current_month
          AND (
            classification.valid_to_month IS NULL
            OR classification.valid_to_month>=fs.current_month
          )
      )
  );

CREATE VIEW IF NOT EXISTS mf_current_stock_monthly_features AS
SELECT feature.*
FROM mf_stock_monthly_features AS feature
JOIN mf_current_stock_month_feature_sets AS current_set
  ON current_set.feature_set_id=feature.feature_set_id;

CREATE VIEW IF NOT EXISTS mf_all_scheme_stock_monthly_features AS
SELECT * FROM mf_current_stock_monthly_features
WHERE feature_scope='all_scheme';

CREATE VIEW IF NOT EXISTS mf_active_equity_stock_monthly_features AS
SELECT * FROM mf_current_stock_monthly_features
WHERE feature_scope='active_equity';
