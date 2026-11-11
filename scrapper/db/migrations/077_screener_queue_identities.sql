-- Preserve historical queue identities without creating operational jobs.
-- Orphans must be explicitly hash-authorized and seeded by queue_identity_recovery.
PRAGMA foreign_keys=OFF;
BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS screener_queue_identities (
    queue_item_id INTEGER PRIMARY KEY,
    identity_basis TEXT NOT NULL CHECK (identity_basis IN ('queue_row', 'historical_reference')),
    evidence_sha256 TEXT,
    CHECK ((identity_basis = 'queue_row' AND evidence_sha256 IS NULL)
        OR (identity_basis = 'historical_reference' AND length(evidence_sha256) = 64 AND evidence_sha256 IS NOT NULL))
);

INSERT INTO screener_queue_identities SELECT queue_item_id, 'queue_row', NULL FROM screener_company_queue WHERE true ON CONFLICT(queue_item_id) DO NOTHING;

CREATE TEMP TABLE queue_identity_migration_guard (approved INTEGER CHECK (approved=1));
INSERT INTO queue_identity_migration_guard SELECT CASE WHEN EXISTS (
    SELECT queue_item_id FROM screener_company_queue_events
    WHERE queue_item_id NOT IN (SELECT queue_item_id FROM screener_queue_identities)
    UNION ALL SELECT queue_item_id FROM exchange_screener_enqueues
    WHERE queue_item_id IS NOT NULL AND queue_item_id NOT IN (SELECT queue_item_id FROM screener_queue_identities)
) THEN 0 ELSE 1 END;
DROP TABLE queue_identity_migration_guard;

CREATE TABLE screener_company_queue_077 (
    queue_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    origin TEXT NOT NULL DEFAULT 'screener_screen'
      CHECK (origin IN ('screener_screen', 'exchange_filing')),
    candidate_id INTEGER REFERENCES screener_result_candidates(candidate_id),
    exchange_filing_id INTEGER REFERENCES exchange_result_filings(filing_id),
    company_path_normalized TEXT NOT NULL,
    reporting_scope TEXT NOT NULL CHECK (reporting_scope IN ('consolidated', 'standalone')),
    result_period TEXT NOT NULL,
    queue_reason TEXT NOT NULL CHECK (queue_reason IN ('new_company', 'result_period_advanced')),
    status TEXT NOT NULL CHECK (status IN (
      'pending', 'claimed', 'running', 'accepted', 'failed_retryable',
      'failed_terminal', 'manual_review', 'skipped_already_accepted'
    )),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_attempt_at_utc TEXT,
    claim_token TEXT UNIQUE,
    claimed_at_utc TEXT,
    claim_expires_at_utc TEXT,
    accepted_parse_id INTEGER REFERENCES parse_artifacts(parse_id),
    accepted_export_path TEXT,
    last_error_json TEXT NOT NULL DEFAULT '{}',
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL, collection_disposition TEXT,
    UNIQUE(company_path_normalized, reporting_scope, result_period),
    CHECK (
      (claim_token IS NULL AND claimed_at_utc IS NULL AND claim_expires_at_utc IS NULL)
      OR (claim_token IS NOT NULL AND claimed_at_utc IS NOT NULL AND claim_expires_at_utc IS NOT NULL)
    ),
    CHECK (
      (origin = 'screener_screen' AND candidate_id IS NOT NULL AND exchange_filing_id IS NULL)
      OR (origin = 'exchange_filing' AND exchange_filing_id IS NOT NULL AND candidate_id IS NULL)
    )
);

INSERT INTO screener_company_queue_077 ("queue_item_id","origin","candidate_id","exchange_filing_id","company_path_normalized","reporting_scope","result_period","queue_reason","status","attempt_count","next_attempt_at_utc","claim_token","claimed_at_utc","claim_expires_at_utc","accepted_parse_id","accepted_export_path","last_error_json","created_at_utc","updated_at_utc","collection_disposition") SELECT "queue_item_id","origin","candidate_id","exchange_filing_id","company_path_normalized","reporting_scope","result_period","queue_reason","status","attempt_count","next_attempt_at_utc","claim_token","claimed_at_utc","claim_expires_at_utc","accepted_parse_id","accepted_export_path","last_error_json","created_at_utc","updated_at_utc","collection_disposition" FROM screener_company_queue;

DROP TABLE screener_company_queue;

ALTER TABLE screener_company_queue_077 RENAME TO screener_company_queue;

CREATE INDEX idx_screener_queue_due
ON screener_company_queue(status, next_attempt_at_utc, claim_expires_at_utc, queue_item_id);

CREATE INDEX idx_screener_queue_origin
ON screener_company_queue(origin, status);

CREATE INDEX idx_screener_queue_status_updated
ON screener_company_queue(status, updated_at_utc DESC);

CREATE TABLE screener_company_queue_events_077 (
    queue_event_id INTEGER PRIMARY KEY,
    queue_item_id INTEGER NOT NULL REFERENCES screener_queue_identities(queue_item_id),
    event_at_utc TEXT NOT NULL,
    from_status TEXT,
    to_status TEXT NOT NULL,
    worker_id TEXT,
    evidence_json TEXT NOT NULL DEFAULT '{}'
);

INSERT INTO screener_company_queue_events_077 ("queue_event_id","queue_item_id","event_at_utc","from_status","to_status","worker_id","evidence_json") SELECT "queue_event_id","queue_item_id","event_at_utc","from_status","to_status","worker_id","evidence_json" FROM screener_company_queue_events;

DROP TABLE screener_company_queue_events;

ALTER TABLE screener_company_queue_events_077 RENAME TO screener_company_queue_events;

CREATE INDEX idx_screener_queue_events_item_time
ON screener_company_queue_events(queue_item_id, event_at_utc, queue_event_id);

CREATE TABLE exchange_screener_enqueues_077 (
    enqueue_id INTEGER PRIMARY KEY,
    filing_id INTEGER NOT NULL REFERENCES exchange_result_filings(filing_id) ON DELETE CASCADE,
    decision TEXT NOT NULL CHECK (decision IN (
      'queued', 'reused', 'unchanged', 'stale', 'already_accepted', 'manual_review'
    )),
    decision_reason TEXT NOT NULL,
    company_path_normalized TEXT,
    reporting_scope TEXT,
    result_period TEXT,
    path_basis TEXT CHECK (path_basis IN ('nse_symbol', 'bse_scrip_code')),
    registry_company_id INTEGER REFERENCES exchange_company_registry(registry_company_id),
    queue_item_id INTEGER REFERENCES screener_queue_identities(queue_item_id),
    created_at_utc TEXT NOT NULL,
    UNIQUE(filing_id)
);

INSERT INTO exchange_screener_enqueues_077 ("enqueue_id","filing_id","decision","decision_reason","company_path_normalized","reporting_scope","result_period","path_basis","registry_company_id","queue_item_id","created_at_utc") SELECT "enqueue_id","filing_id","decision","decision_reason","company_path_normalized","reporting_scope","result_period","path_basis","registry_company_id","queue_item_id","created_at_utc" FROM exchange_screener_enqueues;

DROP TABLE exchange_screener_enqueues;

ALTER TABLE exchange_screener_enqueues_077 RENAME TO exchange_screener_enqueues;

UPDATE sqlite_sequence SET seq=max(seq,coalesce((SELECT max(queue_item_id) FROM screener_queue_identities),0)) WHERE name='screener_company_queue';

INSERT INTO sqlite_sequence(name,seq) SELECT 'screener_company_queue',coalesce(max(queue_item_id),0) FROM screener_queue_identities WHERE NOT EXISTS(SELECT 1 FROM sqlite_sequence WHERE name='screener_company_queue');

CREATE TRIGGER trg_queue_identity_register AFTER INSERT ON screener_company_queue
BEGIN
INSERT INTO screener_queue_identities(queue_item_id,identity_basis,evidence_sha256) VALUES(NEW.queue_item_id,'queue_row',NULL);
END;

CREATE TRIGGER trg_queue_identity_insert_guard BEFORE INSERT ON screener_queue_identities
WHEN NEW.identity_basis <> 'queue_row' OR NOT EXISTS(SELECT 1 FROM screener_company_queue WHERE queue_item_id=NEW.queue_item_id)
BEGIN
SELECT RAISE(ABORT, 'Queue identity registration requires a real queue row');
END;

CREATE TRIGGER trg_queue_identity_update_guard BEFORE UPDATE ON screener_queue_identities
BEGIN
SELECT RAISE(ABORT, 'Queue identity history is immutable');
END;

CREATE TRIGGER trg_queue_identity_delete_guard BEFORE DELETE ON screener_queue_identities
BEGIN
SELECT RAISE(ABORT, 'Queue identity history is immutable');
END;

CREATE TRIGGER trg_queue_key_insert_guard BEFORE INSERT ON screener_company_queue
WHEN EXISTS(SELECT 1 FROM screener_queue_identities WHERE queue_item_id=NEW.queue_item_id)
BEGIN
SELECT RAISE(ABORT, 'Queue identity cannot be reused');
END;

CREATE TRIGGER trg_queue_key_update_guard BEFORE UPDATE OF queue_item_id ON screener_company_queue
WHEN NEW.queue_item_id <> OLD.queue_item_id
BEGIN
SELECT RAISE(ABORT, 'Queue identity is immutable');
END;

CREATE TRIGGER trg_queue_delete_guard BEFORE DELETE ON screener_company_queue
BEGIN
SELECT RAISE(ABORT, 'Queue rows are retained permanently');
END;

CREATE TRIGGER trg_queue_natural_insert_guard BEFORE INSERT ON screener_company_queue
WHEN EXISTS(SELECT 1 FROM screener_company_queue WHERE company_path_normalized=NEW.company_path_normalized AND reporting_scope=NEW.reporting_scope AND result_period=NEW.result_period)
BEGIN
SELECT RAISE(IGNORE);
END;

CREATE TRIGGER trg_queue_natural_update_guard BEFORE UPDATE OF company_path_normalized,reporting_scope,result_period ON screener_company_queue
WHEN EXISTS(SELECT 1 FROM screener_company_queue WHERE company_path_normalized=NEW.company_path_normalized AND reporting_scope=NEW.reporting_scope AND result_period=NEW.result_period AND queue_item_id<>OLD.queue_item_id)
BEGIN
SELECT RAISE(ABORT, 'Queue natural identity conflicts');
END;

CREATE TRIGGER trg_queue_claim_insert_guard BEFORE INSERT ON screener_company_queue
WHEN NEW.claim_token IS NOT NULL AND EXISTS(SELECT 1 FROM screener_company_queue WHERE claim_token=NEW.claim_token AND queue_item_id<>NEW.queue_item_id)
BEGIN
SELECT RAISE(ABORT, 'Queue claim token conflicts');
END;

CREATE TRIGGER trg_queue_claim_update_guard BEFORE UPDATE OF claim_token ON screener_company_queue
WHEN NEW.claim_token IS NOT NULL AND EXISTS(SELECT 1 FROM screener_company_queue WHERE claim_token=NEW.claim_token AND queue_item_id<>NEW.queue_item_id)
BEGIN
SELECT RAISE(ABORT, 'Queue claim token conflicts');
END;

CREATE TRIGGER trg_queue_event_insert_parent BEFORE INSERT ON screener_company_queue_events
WHEN NEW.queue_item_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM screener_company_queue WHERE queue_item_id=NEW.queue_item_id)
BEGIN
SELECT RAISE(ABORT, 'New audit references require a real queue parent');
END;

CREATE TRIGGER trg_queue_event_update_parent BEFORE UPDATE OF queue_item_id ON screener_company_queue_events
WHEN NEW.queue_item_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM screener_company_queue WHERE queue_item_id=NEW.queue_item_id) AND NEW.queue_item_id IS NOT OLD.queue_item_id
BEGIN
SELECT RAISE(ABORT, 'New audit references require a real queue parent');
END;

CREATE TRIGGER trg_queue_enqueue_insert_parent BEFORE INSERT ON exchange_screener_enqueues
WHEN NEW.queue_item_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM screener_company_queue WHERE queue_item_id=NEW.queue_item_id)
BEGIN
SELECT RAISE(ABORT, 'New audit references require a real queue parent');
END;

CREATE TRIGGER trg_queue_enqueue_update_parent BEFORE UPDATE OF queue_item_id ON exchange_screener_enqueues
WHEN NEW.queue_item_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM screener_company_queue WHERE queue_item_id=NEW.queue_item_id) AND NEW.queue_item_id IS NOT OLD.queue_item_id
BEGIN
SELECT RAISE(ABORT, 'New audit references require a real queue parent');
END;

COMMIT;
PRAGMA foreign_keys=ON;
