CREATE TABLE IF NOT EXISTS trendlyne_monthly_pages (
    parse_id INTEGER PRIMARY KEY REFERENCES parse_artifacts(parse_id) ON DELETE CASCADE,
    reporting_scope TEXT NOT NULL DEFAULT 'not_applicable'
        CHECK (reporting_scope = 'not_applicable'),
    source_company_id TEXT NOT NULL,
    source_symbol TEXT NOT NULL,
    source_slug TEXT NOT NULL,
    company_name_raw TEXT NOT NULL,
    bse_code TEXT,
    canonical_url TEXT NOT NULL,
    canonical_source_company_id TEXT NOT NULL,
    canonical_symbol TEXT NOT NULL,
    canonical_period TEXT NOT NULL,
    selected_month_raw TEXT,
    active_month_raw TEXT,
    active_month_url TEXT,
    active_url_period_raw TEXT,
    heading_raw TEXT,
    heading_month_raw TEXT,
    resolved_source_period TEXT
);

CREATE INDEX IF NOT EXISTS ix_trendlyne_monthly_pages_identity_period
ON trendlyne_monthly_pages(source_company_id, source_symbol, resolved_source_period);

