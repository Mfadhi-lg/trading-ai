-- T05 ordered, source-faithful month navigation history. Each parse owns its
-- visible list because the available range can change between observations.
CREATE TABLE IF NOT EXISTS trendlyne_month_links (
    parse_id INTEGER NOT NULL REFERENCES parse_artifacts(parse_id) ON DELETE CASCADE,
    link_position INTEGER NOT NULL,
    period_label_raw TEXT NOT NULL,
    source_period TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_url_period_raw TEXT NOT NULL,
    source_company_id TEXT NOT NULL,
    source_symbol TEXT NOT NULL,
    source_slug TEXT NOT NULL,
    is_active INTEGER NOT NULL CHECK (is_active IN (0, 1)),
    first_observed_at_utc TEXT NOT NULL,
    source_published_at_utc TEXT,
    available_at_utc TEXT NOT NULL,
    availability_basis TEXT NOT NULL,
    PRIMARY KEY(parse_id, link_position),
    UNIQUE(parse_id, source_period),
    UNIQUE(parse_id, source_url)
);

CREATE INDEX IF NOT EXISTS ix_trendlyne_month_links_identity_period
ON trendlyne_month_links(source_company_id, source_period);
