-- A feed can republish different row bytes under the same exchange, security,
-- period, scope and broadcast timestamp. Preserve that evidence for review;
-- the legacy filing_key uniqueness otherwise silently discards the second row.
CREATE TABLE IF NOT EXISTS exchange_result_filing_collisions (
    collision_id INTEGER PRIMARY KEY,
    exchange TEXT NOT NULL CHECK (exchange IN ('NSE', 'BSE')),
    filing_key TEXT NOT NULL,
    existing_filing_id INTEGER NOT NULL REFERENCES exchange_result_filings(filing_id),
    snapshot_id INTEGER NOT NULL REFERENCES exchange_source_snapshots(snapshot_id),
    raw_sha256 TEXT NOT NULL,
    raw_row_json TEXT NOT NULL,
    first_seen_at_utc TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'pending'
      CHECK (review_status IN ('pending', 'resolved')),
    UNIQUE(exchange, filing_key, raw_sha256)
);
CREATE INDEX IF NOT EXISTS idx_exchange_filing_collision_review
ON exchange_result_filing_collisions(review_status, first_seen_at_utc);
