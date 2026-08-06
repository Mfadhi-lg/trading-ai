-- Phase 1 document-link metadata discovered on the Screener company page.
-- These rows describe links only: linked PDFs, presentations and recordings
-- are deliberately not downloaded by this parser.
CREATE TABLE IF NOT EXISTS document_links (
    document_link_id INTEGER PRIMARY KEY,
    parse_id INTEGER NOT NULL REFERENCES parse_artifacts(parse_id) ON DELETE CASCADE,
    reporting_scope TEXT NOT NULL,
    document_type TEXT NOT NULL,
    period_label_raw TEXT,
    title_raw TEXT NOT NULL,
    position INTEGER NOT NULL,
    source_url TEXT,
    publisher TEXT,
    availability_status TEXT NOT NULL CHECK (
      availability_status IN ('available', 'unavailable')
    ),
    first_observed_at_utc TEXT NOT NULL,
    source_published_at_utc TEXT,
    available_at_utc TEXT NOT NULL,
    availability_basis TEXT NOT NULL,
    UNIQUE(parse_id, position)
);
