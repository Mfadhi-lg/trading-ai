-- T04 source claims, source table totals and row-derived monthly activity.
-- Headline values are retained for reconciliation but are explicitly never
-- canonical; canonical activity values are derived from captured fund rows.
CREATE TABLE IF NOT EXISTS trendlyne_mf_page_summaries (
    parse_id INTEGER PRIMARY KEY REFERENCES parse_artifacts(parse_id) ON DELETE CASCADE,
    reporting_scope TEXT NOT NULL DEFAULT 'not_applicable'
        CHECK (reporting_scope = 'not_applicable'),
    period_label_raw TEXT NOT NULL,
    source_period TEXT NOT NULL,
    period_end TEXT NOT NULL,
    headline_raw TEXT NOT NULL,
    headline_display_text TEXT NOT NULL,
    headline_period_label_raw TEXT NOT NULL,
    headline_buyers_display TEXT NOT NULL,
    headline_buyers TEXT NOT NULL,
    headline_sellers_display TEXT NOT NULL,
    headline_sellers TEXT NOT NULL,
    headline_net_change_display TEXT NOT NULL,
    headline_net_change TEXT NOT NULL,
    headline_is_canonical INTEGER NOT NULL DEFAULT 0
        CHECK (headline_is_canonical = 0),
    table_total_shares_display TEXT NOT NULL,
    table_total_shares TEXT NOT NULL,
    table_total_change_display TEXT NOT NULL,
    table_total_change TEXT NOT NULL,
    computed_buyers TEXT NOT NULL,
    computed_sellers TEXT NOT NULL,
    computed_holders TEXT NOT NULL,
    computed_net_change TEXT NOT NULL,
    computed_values_are_canonical INTEGER NOT NULL DEFAULT 1
        CHECK (computed_values_are_canonical = 1),
    computation_basis_json TEXT NOT NULL,
    first_observed_at_utc TEXT NOT NULL,
    source_published_at_utc TEXT,
    available_at_utc TEXT NOT NULL,
    availability_basis TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_trendlyne_mf_page_summaries_period
ON trendlyne_mf_page_summaries(source_period);
