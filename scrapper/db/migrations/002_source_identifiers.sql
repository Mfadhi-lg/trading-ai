CREATE TABLE IF NOT EXISTS source_identifiers (
    source_identifier_id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(company_id),
    source TEXT NOT NULL,
    source_company_id TEXT NOT NULL,
    source_symbol TEXT,
    source_slug TEXT,
    canonical_url TEXT NOT NULL,
    mapping_status TEXT NOT NULL,
    verified_at_utc TEXT NOT NULL,
    UNIQUE(source, source_company_id),
    UNIQUE(company_id, source)
);
