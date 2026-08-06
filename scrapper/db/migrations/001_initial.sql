CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS companies (
    company_id INTEGER PRIMARY KEY,
    current_legal_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS security_identifiers (
    security_identifier_id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(company_id),
    identifier_type TEXT NOT NULL,
    identifier_value TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    UNIQUE(identifier_type, identifier_value, valid_from)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_current_security_identifier
ON security_identifiers(identifier_type, identifier_value)
WHERE is_current = 1;

CREATE TABLE IF NOT EXISTS scrape_runs (
    run_id INTEGER PRIMARY KEY,
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT,
    code_version TEXT NOT NULL,
    config_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running', 'ok', 'error'))
);

CREATE TABLE IF NOT EXISTS response_blobs (
    blob_id INTEGER PRIMARY KEY,
    content_sha256 TEXT NOT NULL UNIQUE,
    content_type TEXT,
    encoding TEXT,
    byte_size INTEGER NOT NULL,
    local_path TEXT NOT NULL,
    first_observed_at_utc TEXT NOT NULL,
    created_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fetch_observations (
    fetch_id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES scrape_runs(run_id),
    company_id INTEGER REFERENCES companies(company_id),
    source TEXT NOT NULL,
    reporting_scope TEXT,
    source_period TEXT NOT NULL DEFAULT '',
    requested_url TEXT NOT NULL,
    final_url TEXT NOT NULL,
    fetched_at_utc TEXT NOT NULL,
    http_status INTEGER NOT NULL,
    etag TEXT,
    last_modified TEXT,
    auth_mode TEXT NOT NULL,
    blob_id INTEGER REFERENCES response_blobs(blob_id),
    fetch_status TEXT NOT NULL CHECK (fetch_status IN ('ok', 'not_modified', 'rejected', 'error'))
);

CREATE TABLE IF NOT EXISTS parse_artifacts (
    parse_id INTEGER PRIMARY KEY,
    blob_id INTEGER NOT NULL REFERENCES response_blobs(blob_id),
    parser_name TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    manifest_version TEXT NOT NULL,
    parsed_at_utc TEXT NOT NULL,
    parse_status TEXT NOT NULL CHECK (parse_status IN ('pending', 'ok', 'partial', 'failed')),
    validation_status TEXT NOT NULL CHECK (validation_status IN ('pending', 'passed', 'passed_with_warnings', 'failed')),
    UNIQUE(blob_id, parser_name, parser_version, manifest_version)
);

CREATE TABLE IF NOT EXISTS source_sections (
    section_id INTEGER PRIMARY KEY,
    parse_id INTEGER NOT NULL REFERENCES parse_artifacts(parse_id) ON DELETE CASCADE,
    section_code TEXT NOT NULL,
    heading_raw TEXT,
    position INTEGER NOT NULL,
    parse_status TEXT NOT NULL,
    error_json TEXT,
    UNIQUE(parse_id, section_code)
);

CREATE TABLE IF NOT EXISTS source_fields (
    section_id INTEGER NOT NULL REFERENCES source_sections(section_id) ON DELETE CASCADE,
    field_position INTEGER NOT NULL,
    field_key_raw TEXT,
    raw_text TEXT NOT NULL,
    normalized_display_text TEXT NOT NULL,
    link_url TEXT,
    field_role TEXT NOT NULL,
    visibility_state TEXT NOT NULL DEFAULT 'visible',
    PRIMARY KEY(section_id, field_position)
);

CREATE TABLE IF NOT EXISTS source_table_cells (
    section_id INTEGER NOT NULL REFERENCES source_sections(section_id) ON DELETE CASCADE,
    table_position INTEGER NOT NULL,
    row_position INTEGER NOT NULL,
    column_position INTEGER NOT NULL,
    row_label_raw TEXT,
    column_label_raw TEXT,
    raw_text TEXT NOT NULL,
    normalized_display_text TEXT NOT NULL,
    link_url TEXT,
    cell_role TEXT NOT NULL,
    visibility_state TEXT NOT NULL DEFAULT 'visible',
    rowspan INTEGER NOT NULL DEFAULT 1,
    colspan INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(section_id, table_position, row_position, column_position)
);

CREATE TABLE IF NOT EXISTS quote_observations (
    parse_id INTEGER PRIMARY KEY REFERENCES parse_artifacts(parse_id) ON DELETE CASCADE,
    display_price TEXT NOT NULL,
    price_decimal TEXT,
    currency TEXT,
    quote_timestamp_raw TEXT,
    quote_timestamp_utc TEXT,
    quote_timezone TEXT,
    quote_timestamp_status TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS company_metric_observations (
    metric_observation_id INTEGER PRIMARY KEY,
    parse_id INTEGER NOT NULL REFERENCES parse_artifacts(parse_id) ON DELETE CASCADE,
    reporting_scope TEXT NOT NULL,
    metric_code TEXT,
    label_raw TEXT NOT NULL,
    position INTEGER NOT NULL,
    display_value TEXT NOT NULL,
    value_decimal TEXT,
    value_json TEXT,
    unit_code TEXT,
    scale_multiplier TEXT,
    missing_kind TEXT,
    UNIQUE(parse_id, position)
);

CREATE TABLE IF NOT EXISTS financial_observations (
    financial_observation_id INTEGER PRIMARY KEY,
    parse_id INTEGER NOT NULL REFERENCES parse_artifacts(parse_id) ON DELETE CASCADE,
    reporting_scope TEXT NOT NULL,
    section_code TEXT NOT NULL,
    line_item_code TEXT,
    line_item_raw TEXT NOT NULL,
    row_position INTEGER NOT NULL,
    period_label_raw TEXT NOT NULL,
    period_end TEXT,
    period_type TEXT NOT NULL,
    fiscal_year INTEGER,
    column_position INTEGER NOT NULL,
    first_observed_at_utc TEXT NOT NULL,
    source_published_at TEXT,
    available_at_utc TEXT NOT NULL,
    availability_basis TEXT NOT NULL,
    display_value TEXT NOT NULL,
    value_decimal TEXT,
    unit_code TEXT,
    scale_multiplier TEXT,
    missing_kind TEXT,
    UNIQUE(parse_id, section_code, row_position, column_position)
);

CREATE TABLE IF NOT EXISTS validation_results (
    validation_id INTEGER PRIMARY KEY,
    parse_id INTEGER NOT NULL REFERENCES parse_artifacts(parse_id) ON DELETE CASCADE,
    rule_code TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'error', 'manual_review')),
    status TEXT NOT NULL CHECK (status IN ('passed', 'failed')),
    expected_value TEXT,
    actual_value TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at_utc TEXT NOT NULL,
    UNIQUE(parse_id, rule_code)
);

CREATE TABLE IF NOT EXISTS accepted_page_versions (
    company_id INTEGER NOT NULL REFERENCES companies(company_id),
    source TEXT NOT NULL,
    reporting_scope TEXT NOT NULL DEFAULT '',
    source_period TEXT NOT NULL DEFAULT '',
    parse_id INTEGER NOT NULL REFERENCES parse_artifacts(parse_id),
    accepted_at_utc TEXT NOT NULL,
    accepted_by TEXT NOT NULL,
    acceptance_mode TEXT NOT NULL,
    PRIMARY KEY(company_id, source, reporting_scope, source_period)
);

CREATE VIEW IF NOT EXISTS latest_accepted_pages AS
SELECT apv.*, pa.blob_id, pa.parser_name, pa.parser_version,
       pa.manifest_version, pa.validation_status
FROM accepted_page_versions AS apv
JOIN parse_artifacts AS pa ON pa.parse_id = apv.parse_id;
