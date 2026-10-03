CREATE TABLE IF NOT EXISTS browser_rating_recovery_workers (
    worker_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    status TEXT NOT NULL,
    heartbeat_at_utc TEXT NOT NULL,
    stopped_at_utc TEXT
);
