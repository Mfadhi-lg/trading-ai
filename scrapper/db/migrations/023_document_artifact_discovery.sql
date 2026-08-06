-- D01 Phase 2 document-artifact identity and discovery provenance.
-- Discovery pages and artifact contents are immutable; only the explicit
-- preferred-content pointer is mutable. Downloading artifact bytes starts in
-- D02, so artifact_contents may legitimately remain empty after discovery.

CREATE TABLE IF NOT EXISTS artifact_discovery_pages (
    discovery_page_id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(company_id),
    discovered_from_source TEXT NOT NULL CHECK (
      discovered_from_source IN (
        'nse', 'bse', 'company_ir', 'rating_agency', 'screener'
      )
    ),
    discovery_page_url TEXT NOT NULL,
    publisher_claim TEXT,
    blob_id INTEGER NOT NULL REFERENCES response_blobs(blob_id),
    parser_name TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    first_observed_at_utc TEXT NOT NULL,
    UNIQUE(
      company_id, discovered_from_source, discovery_page_url, blob_id,
      parser_name, parser_version
    )
);

CREATE TABLE IF NOT EXISTS document_artifacts (
    artifact_id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL REFERENCES companies(company_id),
    artifact_key TEXT NOT NULL,
    artifact_type TEXT NOT NULL CHECK (
      artifact_type IN ('presentation', 'credit_rating', 'transcript', 'recording')
    ),
    publisher TEXT,
    title_raw TEXT NOT NULL,
    event_date TEXT,
    period_end TEXT,
    fiscal_year INTEGER,
    fiscal_quarter INTEGER CHECK (
      fiscal_quarter IS NULL OR fiscal_quarter BETWEEN 1 AND 4
    ),
    published_at_utc TEXT,
    first_observed_at_utc TEXT NOT NULL,
    preferred_content_id INTEGER REFERENCES artifact_contents(content_id),
    acquisition_status TEXT NOT NULL DEFAULT 'discovered' CHECK (
      acquisition_status IN (
        'discovered', 'downloaded', 'validated', 'inaccessible', 'failed'
      )
    ),
    transcript_preference_status TEXT NOT NULL DEFAULT 'not_applicable' CHECK (
      transcript_preference_status IN (
        'not_applicable', 'official_candidate', 'recording_fallback_candidate',
        'official_preferred', 'machine_preferred'
      )
    ),
    UNIQUE(company_id, artifact_type, artifact_key)
);

CREATE TABLE IF NOT EXISTS artifact_sources (
    artifact_source_id INTEGER PRIMARY KEY,
    artifact_id INTEGER NOT NULL REFERENCES document_artifacts(artifact_id),
    discovery_page_id INTEGER NOT NULL
      REFERENCES artifact_discovery_pages(discovery_page_id),
    discovered_from_source TEXT NOT NULL CHECK (
      discovered_from_source IN (
        'nse', 'bse', 'company_ir', 'rating_agency', 'screener'
      )
    ),
    discovery_page_url TEXT NOT NULL,
    document_url TEXT NOT NULL,
    publisher_claim TEXT,
    exchange_announcement_id TEXT,
    source_position INTEGER NOT NULL CHECK (source_position >= 0),
    source_title_raw TEXT NOT NULL,
    classification_evidence_json TEXT NOT NULL,
    is_primary_source INTEGER NOT NULL CHECK (is_primary_source IN (0, 1)),
    first_observed_at_utc TEXT NOT NULL,
    last_checked_at_utc TEXT NOT NULL,
    availability_status TEXT NOT NULL DEFAULT 'available' CHECK (
      availability_status IN (
        'available', 'expected_not_yet_published', 'not_offered',
        'not_applicable', 'source_not_covered', 'inaccessible',
        'discovery_failed', 'unknown'
      )
    ),
    CHECK (
      discovered_from_source <> 'screener'
      OR publisher_claim IS NULL
      OR lower(publisher_claim) <> 'screener'
    ),
    UNIQUE(
      artifact_id, discovered_from_source, discovery_page_url, document_url
    )
);

CREATE TABLE IF NOT EXISTS artifact_contents (
    content_id INTEGER PRIMARY KEY,
    artifact_id INTEGER NOT NULL REFERENCES document_artifacts(artifact_id),
    blob_id INTEGER NOT NULL REFERENCES response_blobs(blob_id),
    media_type TEXT NOT NULL,
    content_sha256 TEXT NOT NULL UNIQUE,
    byte_size INTEGER NOT NULL CHECK (byte_size >= 0),
    extraction_status TEXT NOT NULL DEFAULT 'not_started' CHECK (
      extraction_status IN ('not_started', 'pending', 'ok', 'partial', 'failed')
    ),
    is_official INTEGER NOT NULL CHECK (is_official IN (0, 1)),
    content_origin TEXT NOT NULL CHECK (
      content_origin IN ('publisher_original', 'generated_derivative')
    ),
    created_at_utc TEXT NOT NULL,
    UNIQUE(artifact_id, blob_id)
);

CREATE INDEX IF NOT EXISTS idx_artifact_sources_document_url
ON artifact_sources(document_url);

CREATE INDEX IF NOT EXISTS idx_document_artifacts_company_type
ON document_artifacts(company_id, artifact_type, event_date, period_end);

CREATE TRIGGER IF NOT EXISTS document_artifact_preferred_content_insert_guard
BEFORE INSERT ON document_artifacts
WHEN NEW.preferred_content_id IS NOT NULL
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM artifact_contents
    WHERE content_id=NEW.preferred_content_id AND artifact_id=NEW.artifact_id
  ) THEN RAISE(ABORT, 'preferred content must belong to artifact') END;
END;

CREATE TRIGGER IF NOT EXISTS document_artifact_preferred_content_update_guard
BEFORE UPDATE OF preferred_content_id ON document_artifacts
WHEN NEW.preferred_content_id IS NOT NULL
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM artifact_contents
    WHERE content_id=NEW.preferred_content_id AND artifact_id=NEW.artifact_id
  ) THEN RAISE(ABORT, 'preferred content must belong to artifact') END;
END;

CREATE VIEW IF NOT EXISTS preferred_artifact_contents AS
SELECT artifact.*, content.blob_id, content.media_type, content.content_sha256,
       content.byte_size, content.extraction_status, content.is_official,
       content.content_origin, content.created_at_utc AS content_created_at_utc
FROM document_artifacts AS artifact
JOIN artifact_contents AS content
  ON content.content_id=artifact.preferred_content_id;
