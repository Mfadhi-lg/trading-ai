-- T03 normalized fund-level observations. Raw cells remain canonical source
-- evidence in source_table_cells; this table adds exact decimal strings and
-- typed measurement/period metadata without filtering any scheme row.
CREATE TABLE IF NOT EXISTS trendlyne_fund_observations (
    fund_observation_id INTEGER PRIMARY KEY,
    parse_id INTEGER NOT NULL REFERENCES parse_artifacts(parse_id) ON DELETE CASCADE,
    reporting_scope TEXT NOT NULL DEFAULT 'not_applicable'
        CHECK (reporting_scope = 'not_applicable'),
    table_position INTEGER NOT NULL DEFAULT 0,
    row_position INTEGER NOT NULL,
    column_position INTEGER NOT NULL,
    source_scheme_id TEXT NOT NULL,
    scheme_name_raw TEXT NOT NULL,
    metric_code TEXT NOT NULL CHECK (
        metric_code IN (
            'aum', 'aum_percentage', 'shares_held', 'month_change',
            'month_change_percentage'
        )
    ),
    period_role TEXT NOT NULL CHECK (period_role IN ('current', 'comparison')),
    period_label_raw TEXT NOT NULL,
    source_period TEXT NOT NULL,
    period_end TEXT NOT NULL,
    period_type TEXT NOT NULL CHECK (period_type = 'month'),
    display_value TEXT NOT NULL,
    value_decimal TEXT,
    unit_code TEXT NOT NULL CHECK (
        unit_code IN ('INR_CRORE', 'PERCENT_POINT', 'COUNT')
    ),
    scale_multiplier TEXT NOT NULL,
    missing_kind TEXT,
    first_observed_at_utc TEXT NOT NULL,
    source_published_at_utc TEXT,
    available_at_utc TEXT NOT NULL,
    availability_basis TEXT NOT NULL,
    UNIQUE(parse_id, table_position, row_position, column_position),
    FOREIGN KEY(parse_id, table_position, row_position)
        REFERENCES trendlyne_fund_source_rows(parse_id, table_position, row_position)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_trendlyne_fund_observations_scheme_period
ON trendlyne_fund_observations(source_scheme_id, source_period, metric_code);
