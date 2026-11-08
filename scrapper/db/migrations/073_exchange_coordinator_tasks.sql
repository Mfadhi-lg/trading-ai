-- Additive dispatch inbox. Existing exchange schedule/snapshot/queue history stays intact.
CREATE TABLE exchange_coordinator_tasks (
    task_id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('attempt', 'weekly_report')),
    scheduled_for TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt >= 0),
    priority INTEGER NOT NULL CHECK (priority IN (0, 10, 20)),
    state TEXT NOT NULL DEFAULT 'PENDING' CHECK (state IN ('PENDING', 'RUNNING', 'DONE', 'FAILED')),
    created_at_utc TEXT NOT NULL,
    owner TEXT,
    generation INTEGER NOT NULL DEFAULT 0,
    lease_until_utc TEXT,
    finished_at_utc TEXT,
    result_json TEXT,
    error TEXT NOT NULL DEFAULT '',
    UNIQUE(kind, scheduled_for, attempt),
    CHECK ((kind = 'attempt' AND attempt BETWEEN 1 AND 3) OR
           (kind = 'weekly_report' AND attempt = 0)),
    CHECK ((state = 'RUNNING' AND owner IS NOT NULL AND lease_until_utc IS NOT NULL) OR
           (state != 'RUNNING' AND owner IS NULL AND lease_until_utc IS NULL))
);
CREATE INDEX exchange_coordinator_ready ON exchange_coordinator_tasks(state, priority, scheduled_for, attempt);
