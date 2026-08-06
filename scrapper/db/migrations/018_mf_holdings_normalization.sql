-- M03 normalized official-AMC holdings and auditable security matching.
CREATE TABLE IF NOT EXISTS mf_securities (
    security_id INTEGER PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mf_security_identifiers (
    security_identifier_id INTEGER PRIMARY KEY,
    security_id INTEGER NOT NULL REFERENCES mf_securities(security_id),
    identifier_type TEXT NOT NULL CHECK (identifier_type IN ('isin')),
    identifier_value TEXT NOT NULL,
    structurally_valid INTEGER NOT NULL CHECK (structurally_valid IN (0, 1)),
    first_seen_import_file_id INTEGER NOT NULL
        REFERENCES mf_import_files(import_file_id),
    first_seen_disclosure_month TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    UNIQUE(identifier_type, identifier_value)
);

CREATE TABLE IF NOT EXISTS mf_holdings (
    holding_id INTEGER PRIMARY KEY,
    import_file_id INTEGER NOT NULL
        REFERENCES mf_import_files(import_file_id) ON DELETE CASCADE,
    sheet_id INTEGER NOT NULL
        REFERENCES mf_workbook_sheets(sheet_id) ON DELETE CASCADE,
    source_table_position INTEGER NOT NULL CHECK (source_table_position >= 0),
    source_table_kind TEXT NOT NULL CHECK (
      source_table_kind IN ('primary_portfolio', 'derivatives')
    ),
    source_row INTEGER NOT NULL CHECK (source_row >= 1),
    publisher TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source = 'amc_workbook'),
    source_quality TEXT NOT NULL CHECK (source_quality = 'official'),
    scheme_name_raw TEXT NOT NULL,
    scheme_code TEXT NOT NULL,
    disclosure_month TEXT NOT NULL,
    source_security_code_raw TEXT,
    security_name_raw TEXT NOT NULL,
    isin_raw TEXT,
    isin_normalized TEXT,
    isin_validation_status TEXT NOT NULL CHECK (
      isin_validation_status IN ('valid', 'missing', 'invalid_format', 'invalid_checksum')
    ),
    quantity_decimal TEXT,
    quantity_missing_kind TEXT CHECK (
      quantity_missing_kind IS NULL OR quantity_missing_kind IN ('blank', 'parse_error')
    ),
    market_value_decimal TEXT,
    market_value_missing_kind TEXT CHECK (
      market_value_missing_kind IS NULL OR market_value_missing_kind IN ('blank', 'parse_error')
    ),
    percentage_of_nav_decimal TEXT,
    percentage_of_aum_decimal TEXT,
    percentage_missing_kind TEXT CHECK (
      percentage_missing_kind IS NULL OR percentage_missing_kind IN ('blank', 'parse_error')
    ),
    percentage_qualifier TEXT CHECK (
      percentage_qualifier IS NULL OR percentage_qualifier = 'less_than_0_01'
    ),
    percentage_basis TEXT NOT NULL CHECK (percentage_basis IN ('net_assets', 'aum')),
    instrument_type_raw TEXT NOT NULL,
    instrument_type_code TEXT NOT NULL,
    industry_or_rating_raw TEXT,
    position_direction_raw TEXT,
    currency TEXT NOT NULL CHECK (currency = 'INR'),
    quantity_unit_code TEXT NOT NULL CHECK (quantity_unit_code = 'COUNT'),
    market_value_unit_code TEXT NOT NULL CHECK (market_value_unit_code = 'INR_LAKH'),
    market_value_scale_multiplier TEXT NOT NULL CHECK (market_value_scale_multiplier = '100000'),
    percentage_unit_code TEXT NOT NULL CHECK (percentage_unit_code = 'PERCENT_POINT'),
    normalization_rule_version TEXT NOT NULL,
    first_observed_at_utc TEXT NOT NULL,
    accepted_at_utc TEXT,
    security_id INTEGER REFERENCES mf_securities(security_id),
    security_match_status TEXT NOT NULL CHECK (
      security_match_status IN ('matched', 'unmatched', 'manual_review', 'invalid_isin')
    ),
    UNIQUE(import_file_id, sheet_id, source_table_position, source_row)
);

CREATE TABLE IF NOT EXISTS mf_security_name_evidence (
    name_evidence_id INTEGER PRIMARY KEY,
    security_id INTEGER NOT NULL REFERENCES mf_securities(security_id),
    normalized_name TEXT NOT NULL,
    security_name_raw TEXT NOT NULL,
    import_file_id INTEGER NOT NULL REFERENCES mf_import_files(import_file_id),
    sheet_id INTEGER NOT NULL REFERENCES mf_workbook_sheets(sheet_id),
    source_row INTEGER NOT NULL,
    evidence_basis TEXT NOT NULL CHECK (evidence_basis = 'valid_isin'),
    rule_version TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL,
    UNIQUE(security_id, import_file_id, sheet_id, source_row)
);

CREATE TABLE IF NOT EXISTS mf_security_match_decisions (
    match_decision_id INTEGER PRIMARY KEY,
    holding_id INTEGER NOT NULL REFERENCES mf_holdings(holding_id) ON DELETE CASCADE,
    decision_version INTEGER NOT NULL CHECK (decision_version >= 1),
    is_current INTEGER NOT NULL CHECK (is_current IN (0, 1)),
    security_id INTEGER REFERENCES mf_securities(security_id),
    match_method TEXT NOT NULL CHECK (
      match_method IN ('isin', 'exact_normalized_name', 'none')
    ),
    match_status TEXT NOT NULL CHECK (
      match_status IN ('matched', 'unmatched', 'manual_review', 'invalid_isin')
    ),
    match_key TEXT,
    rule_version TEXT NOT NULL,
    candidate_security_ids_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    decided_at_utc TEXT NOT NULL,
    decided_by TEXT NOT NULL,
    UNIQUE(holding_id, decision_version)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_mf_security_match_current
ON mf_security_match_decisions(holding_id)
WHERE is_current = 1;

CREATE INDEX IF NOT EXISTS ix_mf_holdings_import_scheme
ON mf_holdings(import_file_id, scheme_code, source_row);

CREATE INDEX IF NOT EXISTS ix_mf_holdings_security_month
ON mf_holdings(security_id, disclosure_month)
WHERE security_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_mf_name_evidence_normalized
ON mf_security_name_evidence(normalized_name, security_id);

CREATE VIEW IF NOT EXISTS mf_security_resolved_holdings AS
SELECT h.*
FROM mf_holdings AS h
WHERE h.security_id IS NOT NULL
  AND h.security_match_status = 'matched';

-- M04 acceptance transitions will make rows appear in this view without
-- rewriting M03 normalization or match decisions.
CREATE VIEW IF NOT EXISTS mf_stock_feature_eligible_holdings AS
SELECT h.*
FROM mf_holdings AS h
JOIN mf_import_files AS f ON f.import_file_id = h.import_file_id
WHERE f.import_state = 'accepted'
  AND h.source_table_kind = 'primary_portfolio'
  AND h.security_id IS NOT NULL
  AND h.security_match_status = 'matched';
