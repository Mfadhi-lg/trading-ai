CREATE TABLE IF NOT EXISTS trendlyne_fund_source_rows (
    parse_id INTEGER NOT NULL REFERENCES parse_artifacts(parse_id) ON DELETE CASCADE,
    table_position INTEGER NOT NULL DEFAULT 0,
    row_position INTEGER NOT NULL,
    source_scheme_id TEXT NOT NULL,
    scheme_name_raw TEXT NOT NULL,
    source_url TEXT NOT NULL,
    PRIMARY KEY(parse_id, table_position, row_position),
    UNIQUE(parse_id, source_scheme_id)
);

CREATE INDEX IF NOT EXISTS ix_trendlyne_fund_source_rows_scheme
ON trendlyne_fund_source_rows(source_scheme_id);
