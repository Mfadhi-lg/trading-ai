-- M02 PPFAS workbook structure and source-faithful cells.
ALTER TABLE mf_import_files ADD COLUMN detected_publisher TEXT;
ALTER TABLE mf_import_files ADD COLUMN workbook_manifest_version TEXT;

ALTER TABLE mf_workbook_sheets ADD COLUMN sheet_classification TEXT CHECK (
  sheet_classification IS NULL OR sheet_classification IN ('portfolio', 'non_portfolio')
);
ALTER TABLE mf_workbook_sheets ADD COLUMN manifest_version TEXT;

ALTER TABLE mf_workbook_cells ADD COLUMN cell_position INTEGER;
ALTER TABLE mf_workbook_cells ADD COLUMN table_position INTEGER;

CREATE UNIQUE INDEX IF NOT EXISTS ux_mf_workbook_cells_source_order
ON mf_workbook_cells(sheet_id, cell_position)
WHERE cell_position IS NOT NULL;

CREATE TABLE IF NOT EXISTS mf_workbook_tables (
    table_id INTEGER PRIMARY KEY,
    sheet_id INTEGER NOT NULL
        REFERENCES mf_workbook_sheets(sheet_id) ON DELETE CASCADE,
    table_position INTEGER NOT NULL CHECK (table_position >= 0),
    table_kind TEXT NOT NULL CHECK (
      table_kind IN ('primary_portfolio', 'derivatives', 'supporting_disclosures')
    ),
    heading_raw TEXT NOT NULL,
    start_row INTEGER NOT NULL CHECK (start_row >= 1),
    end_row INTEGER NOT NULL CHECK (end_row >= start_row),
    start_column INTEGER NOT NULL CHECK (start_column >= 1),
    end_column INTEGER NOT NULL CHECK (end_column >= start_column),
    header_start_row INTEGER,
    header_end_row INTEGER,
    source_total_rows_json TEXT NOT NULL,
    required_columns_json TEXT NOT NULL,
    detection_evidence_json TEXT NOT NULL,
    UNIQUE(sheet_id, table_position)
);

CREATE TABLE IF NOT EXISTS mf_workbook_merged_ranges (
    sheet_id INTEGER NOT NULL
        REFERENCES mf_workbook_sheets(sheet_id) ON DELETE CASCADE,
    merge_position INTEGER NOT NULL CHECK (merge_position >= 0),
    range_reference TEXT NOT NULL,
    start_row INTEGER NOT NULL,
    start_column INTEGER NOT NULL,
    end_row INTEGER NOT NULL,
    end_column INTEGER NOT NULL,
    PRIMARY KEY(sheet_id, merge_position),
    UNIQUE(sheet_id, range_reference)
);

CREATE TABLE IF NOT EXISTS mf_workbook_validation_results (
    validation_id INTEGER PRIMARY KEY,
    import_file_id INTEGER NOT NULL
        REFERENCES mf_import_files(import_file_id) ON DELETE CASCADE,
    rule_code TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('error', 'warning')),
    passed INTEGER NOT NULL CHECK (passed IN (0, 1)),
    details_json TEXT NOT NULL,
    UNIQUE(import_file_id, rule_code)
);

CREATE INDEX IF NOT EXISTS ix_mf_workbook_tables_kind
ON mf_workbook_tables(sheet_id, table_kind);
