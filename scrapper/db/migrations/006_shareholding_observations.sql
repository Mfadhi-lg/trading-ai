-- Quarterly (S09) shareholding observations. Yearly rows arrive in S10 and
-- coexist here through the `frequency` column, which is part of row identity so
-- quarterly and yearly data can never overwrite one another.
--
-- Percentages are stored as percentage points (27.20% -> "27.20"); the "No. of
-- Shareholders" row is a COUNT. Each period carries a `classification_regime`
-- tag (`pre_sep_2022`, `xbrl_sep_2022_plus`, or `unknown`) derived from its own
-- period end. An individual row is NEVER tagged `cross_regime`; that label is a
-- derived comparison between two observations (S10), not a property of one row.
CREATE TABLE IF NOT EXISTS shareholding_observations (
    shareholding_observation_id INTEGER PRIMARY KEY,
    parse_id INTEGER NOT NULL REFERENCES parse_artifacts(parse_id) ON DELETE CASCADE,
    reporting_scope TEXT NOT NULL,
    frequency TEXT NOT NULL,
    holder_code TEXT,
    holder_label_raw TEXT NOT NULL,
    row_position INTEGER NOT NULL,
    column_position INTEGER NOT NULL,
    period_label_raw TEXT NOT NULL,
    period_end TEXT,
    display_value TEXT NOT NULL,
    value_decimal TEXT,
    unit_code TEXT,
    classification_regime TEXT NOT NULL,
    missing_kind TEXT,
    first_observed_at_utc TEXT NOT NULL,
    source_published_at_utc TEXT,
    available_at_utc TEXT NOT NULL,
    availability_basis TEXT NOT NULL,
    UNIQUE(parse_id, frequency, row_position, column_position)
);
