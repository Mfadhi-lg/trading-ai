-- A result page is accepted once; its optional concall links may appear later.
-- Keep that wait state separate from the company-period identity.
CREATE TABLE IF NOT EXISTS company_document_followups (
    queue_item_id INTEGER PRIMARY KEY REFERENCES screener_company_queue(queue_item_id),
    status TEXT NOT NULL CHECK (status IN ('pending', 'checking', 'complete', 'not_offered')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_check_at_utc TEXT,
    last_parse_id INTEGER REFERENCES parse_artifacts(parse_id),
    updated_at_utc TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_company_document_followups_due
ON company_document_followups(status, next_check_at_utc, queue_item_id);
