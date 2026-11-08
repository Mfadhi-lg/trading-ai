-- Persist DA-05 host circuits so a worker restart cannot immediately repeat a
-- publisher block.  This complements the rating-specific migration 040 table.

CREATE TABLE artifact_download_host_health (
    host TEXT PRIMARY KEY,
    blocked_until_utc TEXT,
    last_http_status INTEGER,
    consecutive_blocks INTEGER NOT NULL DEFAULT 0,
    last_result_status TEXT,
    retry_after_raw TEXT,
    updated_at_utc TEXT NOT NULL
);

CREATE INDEX idx_artifact_download_host_health_blocked
ON artifact_download_host_health(blocked_until_utc, host);
