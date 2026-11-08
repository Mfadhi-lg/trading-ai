CREATE TABLE IF NOT EXISTS browser_rating_recovery_hosts (
    run_id TEXT NOT NULL,
    target_host TEXT NOT NULL,
    blocked_until_utc TEXT NOT NULL,
    last_http_status INTEGER,
    consecutive_blocks INTEGER NOT NULL DEFAULT 1,
    reason TEXT,
    updated_at_utc TEXT NOT NULL,
    PRIMARY KEY(run_id,target_host)
);
