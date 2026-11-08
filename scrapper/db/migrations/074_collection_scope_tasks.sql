-- Bounded admission batches; existing eligibility and claim history remains authoritative.
CREATE TABLE collection_scope_tasks (
 task_id INTEGER PRIMARY KEY,
 created_at_utc TEXT NOT NULL,
 budget INTEGER NOT NULL CHECK (budget BETWEEN 1 AND 100),
 admitted INTEGER NOT NULL DEFAULT 0 CHECK (admitted >= 0 AND admitted <= budget),
 state TEXT NOT NULL DEFAULT 'PENDING' CHECK (state IN ('PENDING','DONE','FAILED')),
 finished_at_utc TEXT,
 result_json TEXT,
 error TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX collection_scope_single_pending ON collection_scope_tasks(state) WHERE state='PENDING';
