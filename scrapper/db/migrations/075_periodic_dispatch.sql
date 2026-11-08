-- Durable bounded admission, independent underlying work claims remain authoritative.
CREATE TABLE periodic_dispatch_tasks (
 task_id INTEGER PRIMARY KEY,
 kind TEXT NOT NULL CHECK(kind IN ('delayed','recovery','housekeeping')),
 created_at_utc TEXT NOT NULL,
 budget INTEGER NOT NULL CHECK(budget BETWEEN 1 AND 100),
 admitted INTEGER NOT NULL DEFAULT 0 CHECK(admitted BETWEEN 0 AND budget),
 state TEXT NOT NULL DEFAULT 'PENDING' CHECK(state IN ('PENDING','DONE','FAILED')),
 result_cursor INTEGER NOT NULL DEFAULT 0,
 artifact_cursor INTEGER NOT NULL DEFAULT 0,
 finished_at_utc TEXT,
 next_due_at_utc TEXT,
 result_json TEXT,
 error TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX periodic_single_pending ON periodic_dispatch_tasks(kind) WHERE state='PENDING';
