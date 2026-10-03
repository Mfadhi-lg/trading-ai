-- Safety layer and automation surface for exchange universe refreshes (C1-C7).
--
-- Three gaps kept the registry unsafe to automate:
--   C1 a snapshot was only checked row-by-row, so a truncated or reshaped file
--      passed parsing and then rewrote current state;
--   C2 a single absence from one master immediately flipped is_present to 0;
--   C3 operator-imported rows were indistinguishable from officially fetched
--      ones, so the first automated run could silently overwrite them.
--
-- C4/C5 then build on that: sources may arrive compressed, some rows are valid
-- but deliberately not collected, and the combined MII security file is a new
-- dataset. C7 adds the schedule ledger that makes a silent non-publication day
-- visible instead of indistinguishable from a quiet one.
--
-- Two CHECK constraints need SQLite table rebuilds and are done together, with
-- the same PRAGMA foreign_keys=off procedure migration 032 uses.

PRAGMA foreign_keys=off;

-- C1: validation_status gains 'overridden' for the documented escape hatch.
-- C4: compression records how the served bytes were packed, since the stored
--     blob stays byte-identical to what the exchange published.
-- C5: the combined MII security file joins the dataset list.
CREATE TABLE IF NOT EXISTS exchange_source_snapshots_new (
    snapshot_id INTEGER PRIMARY KEY,
    exchange TEXT NOT NULL CHECK (exchange IN ('NSE', 'BSE')),
    dataset TEXT NOT NULL CHECK (dataset IN (
      'nse_equity_master', 'nse_sme_master', 'nse_symbol_change',
      'nse_name_change', 'bse_security_master', 'nse_mii_security_file',
      'nse_financial_results', 'bse_financial_results'
    )),
    effective_date TEXT NOT NULL,
    source_label TEXT NOT NULL,
    requested_url TEXT,
    acquisition_mode TEXT NOT NULL CHECK (acquisition_mode IN ('operator_file', 'fetched')),
    fetch_id INTEGER REFERENCES fetch_observations(fetch_id),
    blob_id INTEGER NOT NULL REFERENCES response_blobs(blob_id),
    parser_name TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    source_encoding TEXT NOT NULL,
    observed_at_utc TEXT NOT NULL,
    header_json TEXT NOT NULL DEFAULT '[]',
    accepted_row_count INTEGER NOT NULL CHECK (accepted_row_count >= 0),
    rejected_row_count INTEGER NOT NULL CHECK (rejected_row_count >= 0),
    validation_status TEXT NOT NULL
      CHECK (validation_status IN ('passed', 'failed', 'overridden')),
    compression TEXT NOT NULL DEFAULT 'none'
      CHECK (compression IN ('none', 'gzip', 'zip')),
    excluded_row_count INTEGER NOT NULL DEFAULT 0 CHECK (excluded_row_count >= 0),
    UNIQUE(dataset, effective_date, blob_id, parser_name, parser_version)
);

INSERT INTO exchange_source_snapshots_new
  (snapshot_id, exchange, dataset, effective_date, source_label, requested_url,
   acquisition_mode, fetch_id, blob_id, parser_name, parser_version,
   source_encoding, observed_at_utc, header_json, accepted_row_count,
   rejected_row_count, validation_status)
SELECT snapshot_id, exchange, dataset, effective_date, source_label, requested_url,
       acquisition_mode, fetch_id, blob_id, parser_name, parser_version,
       source_encoding, observed_at_utc, header_json, accepted_row_count,
       rejected_row_count, validation_status
FROM exchange_source_snapshots;

DROP TABLE exchange_source_snapshots;

ALTER TABLE exchange_source_snapshots_new RENAME TO exchange_source_snapshots;

-- C2/C3: five new event types. Absence is now observed before it is confirmed,
-- and provenance and identity conflicts leave their own trail.
CREATE TABLE IF NOT EXISTS exchange_security_events_new (
    security_event_id INTEGER PRIMARY KEY,
    exchange_security_id INTEGER NOT NULL REFERENCES exchange_securities(exchange_security_id) ON DELETE CASCADE,
    snapshot_id INTEGER REFERENCES exchange_source_snapshots(snapshot_id),
    event_date TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN (
      'added', 'removed', 'reinstated', 'isin_changed', 'name_changed',
      'status_changed', 'symbol_changed', 'attribute_changed',
      'absence_observed', 'absence_confirmed', 'provenance_verified',
      'conflict_detected', 'conflict_resolved'
    )),
    from_value TEXT,
    to_value TEXT,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    recorded_at_utc TEXT NOT NULL
);

INSERT INTO exchange_security_events_new
  (security_event_id, exchange_security_id, snapshot_id, event_date, event_type,
   from_value, to_value, evidence_json, recorded_at_utc)
SELECT security_event_id, exchange_security_id, snapshot_id, event_date, event_type,
       from_value, to_value, evidence_json, recorded_at_utc
FROM exchange_security_events;

DROP TABLE exchange_security_events;

ALTER TABLE exchange_security_events_new RENAME TO exchange_security_events;

CREATE INDEX IF NOT EXISTS idx_exchange_security_events_type
ON exchange_security_events(event_type, event_date);

PRAGMA foreign_keys=on;

-- C1: one verdict per snapshot, with the metrics and thresholds it was judged
-- against, so a past decision stays reproducible after the defaults change.
CREATE TABLE IF NOT EXISTS exchange_snapshot_validations (
    validation_id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES exchange_source_snapshots(snapshot_id) ON DELETE CASCADE,
    baseline_snapshot_id INTEGER REFERENCES exchange_source_snapshots(snapshot_id),
    status TEXT NOT NULL CHECK (status IN ('passed', 'failed', 'overridden')),
    findings_json TEXT NOT NULL DEFAULT '[]',
    metrics_json TEXT NOT NULL DEFAULT '{}',
    thresholds_json TEXT NOT NULL DEFAULT '{}',
    evaluated_at_utc TEXT NOT NULL,
    UNIQUE(snapshot_id)
);

-- C2: absence is a streak of distinct effective dates, not a single miss.
ALTER TABLE exchange_securities ADD COLUMN absence_streak INTEGER NOT NULL DEFAULT 0;
ALTER TABLE exchange_securities ADD COLUMN first_absent_date TEXT;
ALTER TABLE exchange_securities ADD COLUMN last_absent_snapshot_id INTEGER
  REFERENCES exchange_source_snapshots(snapshot_id);

-- C3: how a row entered the registry, and whether an official file has since
-- confirmed it. Existing rows default to manual_import and are then corrected
-- from the acquisition mode of the snapshot that first observed them.
ALTER TABLE exchange_securities ADD COLUMN source_type TEXT NOT NULL DEFAULT 'manual_import'
  CHECK (source_type IN ('official_automated', 'manual_import', 'manual_override'));
ALTER TABLE exchange_securities ADD COLUMN verification_status TEXT NOT NULL DEFAULT 'unverified'
  CHECK (verification_status IN ('verified', 'unverified', 'conflict'));
ALTER TABLE exchange_securities ADD COLUMN official_presence TEXT NOT NULL DEFAULT 'not_confirmed'
  CHECK (official_presence IN ('confirmed', 'not_confirmed'));

UPDATE exchange_securities
SET source_type = 'official_automated',
    verification_status = 'verified',
    official_presence = CASE WHEN is_present = 1 THEN 'confirmed' ELSE 'not_confirmed' END
WHERE first_observed_snapshot_id IN (
  SELECT snapshot_id FROM exchange_source_snapshots WHERE acquisition_mode = 'fetched'
);

-- C3: an identity disagreement between an operator-imported row and an official
-- file is recorded and blocks queueing; it is never resolved by overwriting.
CREATE TABLE IF NOT EXISTS exchange_identity_conflicts (
    conflict_id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES exchange_source_snapshots(snapshot_id),
    exchange_security_id INTEGER REFERENCES exchange_securities(exchange_security_id),
    conflict_type TEXT NOT NULL CHECK (conflict_type IN (
      'isin_mismatch', 'scrip_code_mismatch', 'duplicate_isin', 'segment_mismatch'
    )),
    exchange TEXT NOT NULL,
    exchange_key TEXT NOT NULL,
    existing_value TEXT,
    incoming_value TEXT,
    existing_source_type TEXT,
    resolution TEXT NOT NULL DEFAULT 'open'
      CHECK (resolution IN ('open', 'accepted_official', 'kept_existing', 'dismissed')),
    detected_at_utc TEXT NOT NULL,
    resolved_at_utc TEXT NOT NULL DEFAULT '',
    UNIQUE(exchange, exchange_key, conflict_type, existing_value, incoming_value)
);

CREATE INDEX IF NOT EXISTS idx_exchange_identity_conflicts_open
ON exchange_identity_conflicts(resolution, exchange_security_id);

CREATE INDEX IF NOT EXISTS idx_exchange_securities_verification
ON exchange_securities(verification_status, official_presence);

-- C5: a row that is valid data we deliberately do not collect. An ETF is not a
-- parsing failure, so it is kept here rather than among the rejected rows, and
-- the reconciliation report can show what a file carried that never became a
-- company.
CREATE TABLE IF NOT EXISTS exchange_source_excluded_rows (
    snapshot_id INTEGER NOT NULL REFERENCES exchange_source_snapshots(snapshot_id) ON DELETE CASCADE,
    row_position INTEGER NOT NULL,
    instrument_class TEXT NOT NULL,
    reason_detail TEXT NOT NULL,
    raw_cells_json TEXT NOT NULL,
    PRIMARY KEY(snapshot_id, row_position)
);

CREATE INDEX IF NOT EXISTS idx_exchange_source_excluded_class
ON exchange_source_excluded_rows(instrument_class);

-- C7: one row per dataset per scheduled day per attempt. A day on which an
-- exchange simply did not publish is otherwise indistinguishable from a day the
-- scheduler never ran; this table is what makes that visible.
-- (Superseded by 034, which splits publication from content change and adds the
-- revision role. This shape is kept as written because it is already applied.)
CREATE TABLE IF NOT EXISTS exchange_schedule_runs (
    schedule_run_id INTEGER PRIMARY KEY,
    dataset TEXT NOT NULL,
    scheduled_for TEXT NOT NULL,
    attempt INTEGER NOT NULL CHECK (attempt >= 1),
    outcome TEXT NOT NULL CHECK (outcome IN (
      'refreshed', 'already_refreshed', 'not_published', 'guard_failed',
      'fetch_failed', 'error', 'skipped_no_url', 'unrefreshed'
    )),
    snapshot_id INTEGER REFERENCES exchange_source_snapshots(snapshot_id),
    error TEXT NOT NULL DEFAULT '',
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT NOT NULL,
    UNIQUE(dataset, scheduled_for, attempt)
);

CREATE INDEX IF NOT EXISTS idx_exchange_schedule_runs_day
ON exchange_schedule_runs(scheduled_for, dataset);
