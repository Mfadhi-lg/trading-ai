-- 034: separate "the exchange published" from "the content changed", and give
-- the ledger a place for the revision sweep.
--
-- 033 recorded byte-identical content as `not_published`. That inference is
-- wrong: a security master is republished every trading day and is legitimately
-- unchanged on a day with no listing, delisting, symbol change or series change.
-- Recording such a day as a non-publication kept the retry ladder climbing
-- through 21:00 and 06:00 for a file the exchange had already served, and
-- reported an ordinary Tuesday as an incomplete day.
--
-- The outcomes now say what was observed:
--   published_changed    the artifact exists and its bytes moved
--   published_unchanged  the artifact exists and its bytes did not
--   not_published        the dated artifact is absent (404/410) — the only
--                        evidence of non-publication that does not require
--                        guessing
-- Both `published_` outcomes satisfy the scheduled day.
--
-- `role` distinguishes the day's primary run from the low-frequency re-read of
-- an already-collected reporting period, which is how a late, corrected or
-- restated filing is picked up after its quarter has passed. A revision row
-- carries the period it asked for and never satisfies the scheduled day, so the
-- uniqueness key gains the role.

CREATE TABLE IF NOT EXISTS exchange_schedule_runs_034 (
    schedule_run_id INTEGER PRIMARY KEY,
    dataset TEXT NOT NULL,
    scheduled_for TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt >= 1),
    role TEXT NOT NULL DEFAULT 'primary'
      CHECK (role IN ('primary', 'revision')),
    result_period TEXT NOT NULL DEFAULT '',
    outcome TEXT NOT NULL CHECK (outcome IN (
      'published_changed', 'published_unchanged', 'already_refreshed',
      'not_published', 'guard_failed', 'fetch_failed', 'error',
      'skipped_no_url', 'unrefreshed'
    )),
    snapshot_id INTEGER REFERENCES exchange_source_snapshots(snapshot_id),
    error TEXT NOT NULL DEFAULT '',
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT NOT NULL,
    UNIQUE(dataset, scheduled_for, attempt, role)
);

-- Historic rows are restated, not reinterpreted. A 'refreshed' row was a
-- publication whose content moved. A 'not_published' row cannot be resolved
-- after the fact — the evidence it was inferred from is the blob comparison, not
-- the endpoint — so it is carried over as published_unchanged, which is what the
-- ledger actually observed, with its recorded error text left intact.
INSERT INTO exchange_schedule_runs_034
    (schedule_run_id, dataset, scheduled_for, attempt, role, result_period,
     outcome, snapshot_id, error, started_at_utc, finished_at_utc)
SELECT schedule_run_id, dataset, scheduled_for, attempt, 'primary', '',
       CASE outcome
         WHEN 'refreshed' THEN 'published_changed'
         WHEN 'not_published' THEN 'published_unchanged'
         ELSE outcome
       END,
       snapshot_id, error, started_at_utc, finished_at_utc
FROM exchange_schedule_runs;

DROP TABLE exchange_schedule_runs;

ALTER TABLE exchange_schedule_runs_034 RENAME TO exchange_schedule_runs;

CREATE INDEX IF NOT EXISTS idx_exchange_schedule_runs_day
ON exchange_schedule_runs(scheduled_for, dataset);

CREATE INDEX IF NOT EXISTS idx_exchange_schedule_runs_role
ON exchange_schedule_runs(role, dataset, scheduled_for);
