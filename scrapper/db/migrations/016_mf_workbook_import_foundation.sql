-- M01 local official-AMC workbook import foundation. Source bytes and import
-- observations are append-only; accepted versions are mutable pointers only.
CREATE TABLE IF NOT EXISTS mf_workbook_blobs (
    workbook_blob_id INTEGER PRIMARY KEY,
    content_sha256 TEXT NOT NULL UNIQUE,
    workbook_type TEXT NOT NULL CHECK (workbook_type IN ('xls', 'xlsx', 'unknown')),
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    local_path TEXT NOT NULL,
    first_observed_at_utc TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    safety_status TEXT NOT NULL CHECK (
      safety_status IN ('passed', 'legacy_binary_not_evaluated', 'quarantined')
    ),
    safety_report_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mf_import_files (
    import_file_id INTEGER PRIMARY KEY,
    publisher TEXT NOT NULL,
    requested_disclosure_month TEXT NOT NULL,
    detected_disclosure_month TEXT,
    original_filename TEXT NOT NULL,
    submitted_path TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    workbook_blob_id INTEGER NOT NULL
        REFERENCES mf_workbook_blobs(workbook_blob_id),
    workbook_type TEXT NOT NULL CHECK (workbook_type IN ('xls', 'xlsx', 'unknown')),
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    import_state TEXT NOT NULL CHECK (
      import_state IN ('inbox', 'accepted', 'rejected', 'quarantine')
    ),
    first_observed_at_utc TEXT NOT NULL,
    accepted_at_utc TEXT,
    state_reason_json TEXT NOT NULL,
    UNIQUE(publisher, content_sha256)
);

CREATE TABLE IF NOT EXISTS mf_import_observations (
    import_observation_id INTEGER PRIMARY KEY,
    import_file_id INTEGER NOT NULL
        REFERENCES mf_import_files(import_file_id),
    observed_at_utc TEXT NOT NULL,
    submitted_path TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    requested_publisher TEXT NOT NULL,
    requested_disclosure_month TEXT NOT NULL,
    observation_result TEXT NOT NULL CHECK (
      observation_result IN ('inventoried', 'duplicate', 'quarantined')
    ),
    decision_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mf_workbook_sheets (
    sheet_id INTEGER PRIMARY KEY,
    import_file_id INTEGER NOT NULL
        REFERENCES mf_import_files(import_file_id) ON DELETE CASCADE,
    sheet_position INTEGER NOT NULL CHECK (sheet_position >= 0),
    sheet_name_raw TEXT NOT NULL,
    visibility_state TEXT NOT NULL DEFAULT 'visible' CHECK (
      visibility_state IN ('visible', 'hidden', 'veryHidden', 'unknown')
    ),
    package_part TEXT,
    detected_scheme_code TEXT,
    detected_scheme_name_raw TEXT,
    detection_evidence_json TEXT NOT NULL DEFAULT '{}',
    parse_status TEXT NOT NULL CHECK (
      parse_status IN ('inventoried', 'pending', 'ok', 'failed', 'skipped')
    ),
    table_start_row INTEGER,
    table_end_row INTEGER,
    UNIQUE(import_file_id, sheet_position)
);

CREATE TABLE IF NOT EXISTS mf_workbook_cells (
    sheet_id INTEGER NOT NULL
        REFERENCES mf_workbook_sheets(sheet_id) ON DELETE CASCADE,
    row_position INTEGER NOT NULL CHECK (row_position >= 1),
    column_position INTEGER NOT NULL CHECK (column_position >= 1),
    coordinate TEXT NOT NULL,
    raw_value TEXT,
    display_value TEXT,
    formula TEXT,
    data_type TEXT,
    number_format TEXT,
    cell_role TEXT,
    normalized_field_code TEXT,
    PRIMARY KEY(sheet_id, row_position, column_position)
);

CREATE TABLE IF NOT EXISTS mf_import_state_events (
    state_event_id INTEGER PRIMARY KEY,
    import_file_id INTEGER NOT NULL
        REFERENCES mf_import_files(import_file_id),
    from_state TEXT CHECK (
      from_state IS NULL OR from_state IN ('inbox', 'accepted', 'rejected', 'quarantine')
    ),
    to_state TEXT NOT NULL CHECK (
      to_state IN ('inbox', 'accepted', 'rejected', 'quarantine')
    ),
    changed_at_utc TEXT NOT NULL,
    changed_by TEXT NOT NULL,
    reason_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mf_schemes (
    scheme_id INTEGER PRIMARY KEY,
    publisher TEXT NOT NULL,
    scheme_code TEXT NOT NULL,
    current_name TEXT NOT NULL,
    UNIQUE(publisher, scheme_code)
);

CREATE TABLE IF NOT EXISTS mf_accepted_versions (
    publisher TEXT NOT NULL,
    scheme_id INTEGER NOT NULL REFERENCES mf_schemes(scheme_id),
    disclosure_month TEXT NOT NULL,
    import_file_id INTEGER NOT NULL REFERENCES mf_import_files(import_file_id),
    accepted_at_utc TEXT NOT NULL,
    accepted_by TEXT NOT NULL,
    acceptance_mode TEXT NOT NULL,
    PRIMARY KEY(publisher, scheme_id, disclosure_month)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS ix_mf_import_files_state
ON mf_import_files(publisher, requested_disclosure_month, import_state);

CREATE INDEX IF NOT EXISTS ix_mf_import_observations_hash
ON mf_import_observations(import_file_id, observed_at_utc);
