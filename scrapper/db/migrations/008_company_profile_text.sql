-- S12: Company-profile links and non-numeric About/Pros/Cons prose.
CREATE TABLE IF NOT EXISTS company_profile_links (
    company_profile_link_id INTEGER PRIMARY KEY,
    parse_id INTEGER NOT NULL REFERENCES parse_artifacts(parse_id) ON DELETE CASCADE,
    reporting_scope TEXT NOT NULL,
    link_type TEXT NOT NULL,
    label_raw TEXT NOT NULL,
    identifier_value TEXT,
    position INTEGER NOT NULL,
    source_url TEXT NOT NULL,
    first_observed_at_utc TEXT NOT NULL,
    source_published_at_utc TEXT,
    available_at_utc TEXT NOT NULL,
    availability_basis TEXT NOT NULL,
    UNIQUE(parse_id, position)
);

CREATE TABLE IF NOT EXISTS company_text_observations (
    company_text_observation_id INTEGER PRIMARY KEY,
    parse_id INTEGER NOT NULL REFERENCES parse_artifacts(parse_id) ON DELETE CASCADE,
    reporting_scope TEXT NOT NULL,
    text_type TEXT NOT NULL,
    heading_raw TEXT NOT NULL,
    position INTEGER NOT NULL,
    display_text TEXT NOT NULL,
    content_origin TEXT NOT NULL,
    is_source_generated INTEGER NOT NULL CHECK (is_source_generated IN (0, 1)),
    first_observed_at_utc TEXT NOT NULL,
    source_published_at_utc TEXT,
    available_at_utc TEXT NOT NULL,
    availability_basis TEXT NOT NULL,
    UNIQUE(parse_id, text_type, position)
);
