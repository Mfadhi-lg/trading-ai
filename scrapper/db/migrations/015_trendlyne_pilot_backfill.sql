-- P03 durable, bounded Trendlyne pilot backfill plans. The plan body is
-- immutable; item state is resumable and is reconciled with accepted pointers.
CREATE TABLE IF NOT EXISTS trendlyne_backfill_plans (
    plan_id INTEGER PRIMARY KEY,
    plan_key TEXT NOT NULL UNIQUE,
    pilot_code TEXT NOT NULL CHECK (pilot_code = 'P03'),
    end_source_period TEXT NOT NULL,
    month_count INTEGER NOT NULL CHECK (month_count BETWEEN 6 AND 12),
    company_count INTEGER NOT NULL CHECK (company_count > 0),
    request_count INTEGER NOT NULL CHECK (request_count > 0),
    batch_size INTEGER NOT NULL CHECK (batch_size BETWEEN 1 AND 3),
    plan_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
      status IN ('pending', 'in_progress', 'complete', 'failed')
    ),
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trendlyne_backfill_items (
    plan_id INTEGER NOT NULL
        REFERENCES trendlyne_backfill_plans(plan_id) ON DELETE CASCADE,
    request_position INTEGER NOT NULL,
    company_id INTEGER REFERENCES companies(company_id),
    source_company_id TEXT NOT NULL,
    source_symbol TEXT NOT NULL,
    source_slug TEXT NOT NULL,
    requested_source_period TEXT NOT NULL,
    requested_url TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
      status IN ('pending', 'running', 'accepted', 'failed')
    ),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    last_started_at_utc TEXT,
    completed_at_utc TEXT,
    fetch_id INTEGER REFERENCES fetch_observations(fetch_id),
    parse_id INTEGER REFERENCES parse_artifacts(parse_id),
    last_error_json TEXT,
    PRIMARY KEY(plan_id, request_position),
    UNIQUE(plan_id, source_symbol, requested_source_period),
    UNIQUE(plan_id, requested_url)
);

CREATE INDEX IF NOT EXISTS ix_trendlyne_backfill_items_status
ON trendlyne_backfill_items(plan_id, status, request_position);
