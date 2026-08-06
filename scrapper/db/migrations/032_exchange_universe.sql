-- Durable NSE/BSE security registry, dated snapshots, corporate changes,
-- incremental result-filing discovery, and the exchange->Screener queue bridge.
--
-- Universe discovery moves from Screener screens to official exchange security
-- masters. Screener remains the detailed company-page source only. Securities
-- are unioned by ISIN; NSE symbols and BSE scrip codes stay separate columns and
-- are never merged from name text.

CREATE TABLE IF NOT EXISTS exchange_source_snapshots (
    snapshot_id INTEGER PRIMARY KEY,
    exchange TEXT NOT NULL CHECK (exchange IN ('NSE', 'BSE')),
    dataset TEXT NOT NULL CHECK (dataset IN (
      'nse_equity_master', 'nse_sme_master', 'nse_symbol_change',
      'nse_name_change', 'bse_security_master',
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
    validation_status TEXT NOT NULL CHECK (validation_status IN ('passed', 'failed')),
    UNIQUE(dataset, effective_date, blob_id, parser_name, parser_version)
);

-- Every rejected source row is retained verbatim for review; it never silently
-- disappears from a dated snapshot.
CREATE TABLE IF NOT EXISTS exchange_source_rejected_rows (
    snapshot_id INTEGER NOT NULL REFERENCES exchange_source_snapshots(snapshot_id) ON DELETE CASCADE,
    row_position INTEGER NOT NULL,
    reason_code TEXT NOT NULL,
    reason_detail TEXT NOT NULL,
    raw_cells_json TEXT NOT NULL,
    PRIMARY KEY(snapshot_id, row_position)
);

CREATE TABLE IF NOT EXISTS exchange_securities (
    exchange_security_id INTEGER PRIMARY KEY,
    exchange TEXT NOT NULL CHECK (exchange IN ('NSE', 'BSE')),
    segment TEXT NOT NULL CHECK (segment IN ('equity', 'sme')),
    exchange_key TEXT NOT NULL,
    nse_symbol TEXT,
    bse_scrip_code TEXT,
    current_isin TEXT,
    current_name_raw TEXT NOT NULL,
    current_status TEXT NOT NULL CHECK (current_status IN (
      'active', 'suspended', 'delisted', 'absent_from_master', 'unknown'
    )),
    current_version_id INTEGER,
    is_present INTEGER NOT NULL DEFAULT 1 CHECK (is_present IN (0, 1)),
    first_observed_date TEXT NOT NULL,
    first_observed_snapshot_id INTEGER NOT NULL REFERENCES exchange_source_snapshots(snapshot_id),
    last_observed_date TEXT NOT NULL,
    last_observed_snapshot_id INTEGER NOT NULL REFERENCES exchange_source_snapshots(snapshot_id),
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL,
    UNIQUE(exchange, segment, exchange_key),
    CHECK (
      (exchange = 'NSE' AND nse_symbol IS NOT NULL AND bse_scrip_code IS NULL)
      OR (exchange = 'BSE' AND bse_scrip_code IS NOT NULL AND nse_symbol IS NULL)
    )
);

-- Append-only effective-dated attribute history. A new version is written only
-- when the normalized attribute tuple actually changes.
CREATE TABLE IF NOT EXISTS exchange_security_versions (
    version_id INTEGER PRIMARY KEY,
    exchange_security_id INTEGER NOT NULL REFERENCES exchange_securities(exchange_security_id) ON DELETE CASCADE,
    snapshot_id INTEGER NOT NULL REFERENCES exchange_source_snapshots(snapshot_id),
    isin TEXT,
    name_raw TEXT NOT NULL,
    status TEXT NOT NULL,
    status_raw TEXT,
    series_or_group TEXT,
    instrument_raw TEXT,
    face_value_raw TEXT,
    listing_date TEXT,
    attributes_json TEXT NOT NULL DEFAULT '{}',
    row_sha256 TEXT NOT NULL,
    valid_from_date TEXT NOT NULL,
    valid_to_date TEXT,
    recorded_at_utc TEXT NOT NULL,
    UNIQUE(exchange_security_id, valid_from_date)
);

CREATE TABLE IF NOT EXISTS exchange_security_events (
    security_event_id INTEGER PRIMARY KEY,
    exchange_security_id INTEGER NOT NULL REFERENCES exchange_securities(exchange_security_id) ON DELETE CASCADE,
    snapshot_id INTEGER REFERENCES exchange_source_snapshots(snapshot_id),
    event_date TEXT NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN (
      'added', 'removed', 'reinstated', 'isin_changed', 'name_changed',
      'status_changed', 'symbol_changed', 'attribute_changed'
    )),
    from_value TEXT,
    to_value TEXT,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    recorded_at_utc TEXT NOT NULL
);

-- ISIN is the only permitted union key across exchanges.
CREATE TABLE IF NOT EXISTS exchange_company_registry (
    registry_company_id INTEGER PRIMARY KEY,
    isin TEXT NOT NULL UNIQUE,
    canonical_name_raw TEXT NOT NULL,
    company_id INTEGER REFERENCES companies(company_id),
    first_observed_date TEXT NOT NULL,
    last_observed_date TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS exchange_company_links (
    link_id INTEGER PRIMARY KEY,
    registry_company_id INTEGER NOT NULL REFERENCES exchange_company_registry(registry_company_id) ON DELETE CASCADE,
    exchange_security_id INTEGER NOT NULL REFERENCES exchange_securities(exchange_security_id) ON DELETE CASCADE,
    link_basis TEXT NOT NULL CHECK (link_basis IN ('isin')),
    linked_from_date TEXT NOT NULL,
    unlinked_from_date TEXT,
    is_current INTEGER NOT NULL DEFAULT 1 CHECK (is_current IN (0, 1)),
    created_at_utc TEXT NOT NULL,
    UNIQUE(registry_company_id, exchange_security_id, linked_from_date)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_exchange_company_link_current
ON exchange_company_links(exchange_security_id) WHERE is_current = 1;

CREATE TABLE IF NOT EXISTS exchange_symbol_changes (
    symbol_change_id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES exchange_source_snapshots(snapshot_id),
    exchange TEXT NOT NULL CHECK (exchange IN ('NSE', 'BSE')),
    change_kind TEXT NOT NULL CHECK (change_kind IN ('symbol', 'name')),
    company_name_raw TEXT NOT NULL,
    old_value TEXT NOT NULL,
    new_value TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    applied_status TEXT NOT NULL CHECK (applied_status IN ('applied', 'unmatched')),
    exchange_security_id INTEGER REFERENCES exchange_securities(exchange_security_id),
    recorded_at_utc TEXT NOT NULL,
    UNIQUE(exchange, change_kind, old_value, new_value, effective_date)
);

CREATE TABLE IF NOT EXISTS exchange_result_filings (
    filing_id INTEGER PRIMARY KEY,
    snapshot_id INTEGER NOT NULL REFERENCES exchange_source_snapshots(snapshot_id),
    exchange TEXT NOT NULL CHECK (exchange IN ('NSE', 'BSE')),
    filing_key TEXT NOT NULL,
    exchange_key TEXT NOT NULL,
    isin TEXT,
    company_name_raw TEXT NOT NULL,
    result_period TEXT NOT NULL,
    period_end_date TEXT NOT NULL,
    reporting_scope TEXT NOT NULL CHECK (reporting_scope IN ('consolidated', 'standalone')),
    audited_raw TEXT,
    cumulative_raw TEXT,
    relating_to_raw TEXT,
    broadcast_raw TEXT NOT NULL,
    broadcast_at_utc TEXT NOT NULL,
    raw_row_json TEXT NOT NULL,
    discovered_at_utc TEXT NOT NULL,
    UNIQUE(exchange, filing_key)
);

-- Incremental polling watermark; only filings broadcast after it are new work.
CREATE TABLE IF NOT EXISTS exchange_result_watermarks (
    exchange TEXT NOT NULL,
    dataset TEXT NOT NULL,
    last_broadcast_at_utc TEXT NOT NULL,
    last_snapshot_id INTEGER REFERENCES exchange_source_snapshots(snapshot_id),
    updated_at_utc TEXT NOT NULL,
    PRIMARY KEY(exchange, dataset)
);

CREATE TABLE IF NOT EXISTS exchange_company_result_state (
    exchange TEXT NOT NULL,
    exchange_key TEXT NOT NULL,
    reporting_scope TEXT NOT NULL CHECK (reporting_scope IN ('consolidated', 'standalone')),
    latest_filed_result_period TEXT NOT NULL,
    latest_broadcast_at_utc TEXT NOT NULL,
    latest_filing_id INTEGER NOT NULL REFERENCES exchange_result_filings(filing_id),
    latest_queued_result_period TEXT,
    updated_at_utc TEXT NOT NULL,
    PRIMARY KEY(exchange, exchange_key, reporting_scope)
);

-- Every filing's queue decision is recorded, including the ones that create no
-- work, so discovery gaps stay distinguishable from technical failures.
CREATE TABLE IF NOT EXISTS exchange_screener_enqueues (
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
    queue_item_id INTEGER REFERENCES screener_company_queue(queue_item_id),
    created_at_utc TEXT NOT NULL,
    UNIQUE(filing_id)
);

CREATE INDEX IF NOT EXISTS idx_exchange_securities_isin
ON exchange_securities(current_isin, is_present);

CREATE INDEX IF NOT EXISTS idx_exchange_security_events_type
ON exchange_security_events(event_type, event_date);

CREATE INDEX IF NOT EXISTS idx_exchange_result_filings_broadcast
ON exchange_result_filings(exchange, broadcast_at_utc);

CREATE INDEX IF NOT EXISTS idx_exchange_result_filings_period
ON exchange_result_filings(result_period, reporting_scope);

-- Rebuild the Screener queue so exchange-discovered work shares one worker,
-- one lease protocol and one event history with screen-discovered work.
-- Follows the documented SQLite table-rebuild procedure so that
-- screener_company_queue_events keeps referencing the live table by name.
PRAGMA foreign_keys=off;

CREATE TABLE IF NOT EXISTS screener_company_queue_new (
    queue_item_id INTEGER PRIMARY KEY,
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
    updated_at_utc TEXT NOT NULL,
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

INSERT INTO screener_company_queue_new
  (queue_item_id, origin, candidate_id, exchange_filing_id,
   company_path_normalized, reporting_scope, result_period, queue_reason,
   status, attempt_count, next_attempt_at_utc, claim_token, claimed_at_utc,
   claim_expires_at_utc, accepted_parse_id, accepted_export_path,
   last_error_json, created_at_utc, updated_at_utc)
SELECT queue_item_id, 'screener_screen', candidate_id, NULL,
       company_path_normalized, reporting_scope, result_period, queue_reason,
       status, attempt_count, next_attempt_at_utc, claim_token, claimed_at_utc,
       claim_expires_at_utc, accepted_parse_id, accepted_export_path,
       last_error_json, created_at_utc, updated_at_utc
FROM screener_company_queue;

DROP TABLE screener_company_queue;

ALTER TABLE screener_company_queue_new RENAME TO screener_company_queue;

CREATE INDEX IF NOT EXISTS idx_screener_queue_due
ON screener_company_queue(status, next_attempt_at_utc, claim_expires_at_utc, queue_item_id);

CREATE INDEX IF NOT EXISTS idx_screener_queue_origin
ON screener_company_queue(origin, status);

PRAGMA foreign_keys=on;
