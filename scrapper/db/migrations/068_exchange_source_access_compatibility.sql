-- C04.3 additive reconciliation: works with or without historical A2 065.
-- Preserve existing BLOCKED/RESUMED evidence; never re-seed a resumed dataset.
BEGIN IMMEDIATE;
-- Keep source access holds separate from publication and filing state.
-- Rebuild the existing schedule ledger only to admit the new, explicit outcome.
CREATE TABLE exchange_schedule_runs_068 (
    schedule_run_id INTEGER PRIMARY KEY,
    dataset TEXT NOT NULL,
    scheduled_for TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt >= 1),
    role TEXT NOT NULL DEFAULT 'primary' CHECK (role IN ('primary', 'revision')),
    result_period TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL CHECK (outcome IN (
      'published_changed', 'published_unchanged', 'already_refreshed',
      'not_published', 'guard_failed', 'fetch_failed', 'error',
      'skipped_no_url', 'unrefreshed', 'source_access_blocked'
    )),
    snapshot_id INTEGER REFERENCES exchange_source_snapshots(snapshot_id),
    error TEXT NOT NULL DEFAULT '',
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT NOT NULL,
    UNIQUE(dataset, scheduled_for, attempt, role)
);
INSERT INTO exchange_schedule_runs_068
SELECT * FROM exchange_schedule_runs;
DROP TABLE exchange_schedule_runs;
ALTER TABLE exchange_schedule_runs_068 RENAME TO exchange_schedule_runs;
CREATE INDEX idx_exchange_schedule_runs_day
ON exchange_schedule_runs(scheduled_for, dataset);
CREATE INDEX idx_exchange_schedule_runs_role
ON exchange_schedule_runs(role, dataset, scheduled_for);

CREATE TABLE IF NOT EXISTS exchange_source_access_events (
    event_id INTEGER PRIMARY KEY,
    dataset TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('BLOCKED', 'RESUMED')),
    event_at_utc TEXT NOT NULL,
    evidence TEXT NOT NULL,
    UNIQUE(dataset, action, event_at_utc)
);
CREATE INDEX IF NOT EXISTS idx_exchange_source_access_events_latest
ON exchange_source_access_events(dataset, event_id DESC);

-- The last actual BSE fetch in the existing ledger may already be a 403.
-- Ignore synthetic unrefreshed rows, and do not turn any 404 or later success
-- into an access hold. This uses only stored response evidence; no request runs.
INSERT INTO exchange_source_access_events(dataset, action, event_at_utc, evidence)
SELECT run.dataset, 'BLOCKED', run.finished_at_utc,
       'SOURCE_ACCESS_BLOCKED: prior scheduler HTTP 403'
FROM exchange_schedule_runs AS run
WHERE NOT EXISTS (SELECT 1 FROM exchange_source_access_events AS existing WHERE existing.dataset=run.dataset)
  AND run.dataset LIKE 'bse_%'
  AND run.schedule_run_id = (
      SELECT later.schedule_run_id FROM exchange_schedule_runs AS later
      WHERE later.dataset=run.dataset
        AND later.outcome NOT IN ('unrefreshed', 'already_refreshed', 'skipped_no_url')
      ORDER BY later.finished_at_utc DESC, later.schedule_run_id DESC LIMIT 1
  )
  AND run.outcome='fetch_failed'
  AND run.error LIKE '%status=403%';

COMMIT;
