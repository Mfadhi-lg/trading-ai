-- Persist host circuit breakers so restarting a worker cannot hammer a blocked agency.

ALTER TABLE credit_rating_download_queue
ADD COLUMN target_host TEXT NOT NULL DEFAULT '';

CREATE TABLE IF NOT EXISTS credit_rating_host_health (
    host TEXT PRIMARY KEY,
    blocked_until_utc TEXT,
    last_http_status INTEGER,
    consecutive_blocks INTEGER NOT NULL DEFAULT 0,
    last_result_status TEXT,
    updated_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_credit_rating_queue_host_claim
ON credit_rating_download_queue(target_host, status, next_attempt_at_utc);
