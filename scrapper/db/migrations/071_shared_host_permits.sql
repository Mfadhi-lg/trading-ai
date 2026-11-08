-- C20.1 additive durable contract only; executors are not connected yet.
-- No updates to existing queues, attempts, publisher policy, bytes or history.
CREATE TABLE acquisition_host_state (
    publisher_group TEXT PRIMARY KEY,
    next_request_at REAL NOT NULL DEFAULT 0,
    blocked_until REAL NOT NULL DEFAULT 0,
    circuit_until REAL NOT NULL DEFAULT 0,
    observed_at REAL NOT NULL DEFAULT 0,
    consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK(consecutive_failures >= 0),
    retry_after_raw TEXT,
    last_outcome TEXT,
    policy_version TEXT NOT NULL
);
CREATE TABLE acquisition_host_permits (
    permit_token TEXT PRIMARY KEY,
    publisher_group TEXT NOT NULL REFERENCES acquisition_host_state(publisher_group),
    host TEXT NOT NULL,
    acquisition_mode TEXT NOT NULL CHECK(acquisition_mode IN ('generic','rating','browser','screener')),
    owner TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ACTIVE','RELEASED')),
    acquired_at REAL NOT NULL,
    lease_expires_at REAL NOT NULL,
    released_at REAL,
    policy_version TEXT NOT NULL,
    authorization_json TEXT NOT NULL DEFAULT '{}',
    CHECK(lease_expires_at > acquired_at)
);
CREATE INDEX idx_acquisition_host_active
ON acquisition_host_permits(publisher_group,status,lease_expires_at);
