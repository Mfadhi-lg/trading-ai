BEGIN IMMEDIATE;

ALTER TABLE fetch_observations
ADD COLUMN resource_kind TEXT NOT NULL DEFAULT 'company_page';

CREATE TABLE supplemental_parse_links (
    parent_parse_id INTEGER NOT NULL
        REFERENCES parse_artifacts(parse_id) ON DELETE CASCADE,
    component_code TEXT NOT NULL,
    supplemental_parse_id INTEGER NOT NULL
        REFERENCES parse_artifacts(parse_id),
    PRIMARY KEY(parent_parse_id, component_code),
    UNIQUE(parent_parse_id, supplemental_parse_id)
) WITHOUT ROWID;

CREATE TABLE peer_observations (
    peer_observation_id INTEGER PRIMARY KEY,
    parse_id INTEGER NOT NULL
        REFERENCES parse_artifacts(parse_id) ON DELETE CASCADE,
    reporting_scope TEXT NOT NULL,
    row_kind TEXT NOT NULL CHECK (row_kind IN ('company', 'median')),
    peer_source_company_id TEXT,
    peer_name_raw TEXT NOT NULL,
    peer_source_url TEXT,
    is_subject_company INTEGER NOT NULL DEFAULT 0
        CHECK (is_subject_company IN (0, 1)),
    row_position INTEGER NOT NULL,
    column_position INTEGER NOT NULL,
    metric_code TEXT,
    metric_label_raw TEXT NOT NULL,
    display_value TEXT NOT NULL,
    value_decimal TEXT,
    unit_code TEXT,
    scale_multiplier TEXT,
    missing_kind TEXT,
    first_observed_at_utc TEXT NOT NULL,
    source_published_at_utc TEXT,
    available_at_utc TEXT NOT NULL,
    availability_basis TEXT NOT NULL,
    UNIQUE(parse_id, row_position, column_position)
);

CREATE INDEX idx_peer_observations_parse_row
ON peer_observations(parse_id, row_position, column_position);

COMMIT;
