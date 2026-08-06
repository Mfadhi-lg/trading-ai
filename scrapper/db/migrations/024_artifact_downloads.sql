-- D02 bounded document/media downloads and complete URL provenance.

CREATE TABLE IF NOT EXISTS artifact_download_attempts (
    download_attempt_id INTEGER PRIMARY KEY,
    artifact_id INTEGER NOT NULL REFERENCES document_artifacts(artifact_id),
    requested_url TEXT NOT NULL,
    final_url TEXT,
    started_at_utc TEXT NOT NULL,
    fetched_at_utc TEXT NOT NULL,
    elapsed_ms INTEGER NOT NULL CHECK (elapsed_ms >= 0),
    http_status INTEGER,
    response_headers_json TEXT NOT NULL DEFAULT '{}',
    redirect_chain_json TEXT NOT NULL DEFAULT '[]',
    declared_media_type TEXT,
    detected_media_type TEXT,
    byte_size INTEGER CHECK (byte_size IS NULL OR byte_size >= 0),
    content_sha256 TEXT,
    blob_id INTEGER REFERENCES response_blobs(blob_id),
    content_id INTEGER REFERENCES artifact_contents(content_id),
    result_status TEXT NOT NULL CHECK (
      result_status IN (
        'validated', 'rejected_http_status', 'rejected_unexpected_html',
        'rejected_unknown_signature', 'rejected_declared_media_type',
        'rejected_artifact_type', 'too_large', 'timed_out', 'network_error'
      )
    ),
    limits_json TEXT NOT NULL,
    error_json TEXT NOT NULL DEFAULT '{}',
    CHECK (
      result_status <> 'validated'
      OR (
        http_status = 200 AND detected_media_type IS NOT NULL
        AND content_sha256 IS NOT NULL AND blob_id IS NOT NULL
        AND content_id IS NOT NULL
      )
    ),
    CHECK (content_id IS NULL OR result_status = 'validated')
);

-- One HTTP request may represent the same URL discovered on more than one
-- page (for example, a BSE filing repeated by Screener). This join retains
-- every discovery path without issuing a redundant request.
CREATE TABLE IF NOT EXISTS artifact_download_attempt_sources (
    download_attempt_id INTEGER NOT NULL
      REFERENCES artifact_download_attempts(download_attempt_id),
    artifact_source_id INTEGER NOT NULL REFERENCES artifact_sources(artifact_source_id),
    PRIMARY KEY(download_attempt_id, artifact_source_id)
);

CREATE INDEX IF NOT EXISTS idx_artifact_download_attempts_artifact
ON artifact_download_attempts(artifact_id, download_attempt_id);

CREATE INDEX IF NOT EXISTS idx_artifact_download_attempts_hash
ON artifact_download_attempts(content_sha256);

CREATE INDEX IF NOT EXISTS idx_artifact_download_sources_source
ON artifact_download_attempt_sources(artifact_source_id, download_attempt_id);

CREATE VIEW IF NOT EXISTS artifact_download_provenance AS
SELECT attempt.*, source.artifact_source_id, source.discovered_from_source,
       source.discovery_page_url, source.document_url, source.is_primary_source
FROM artifact_download_attempts AS attempt
JOIN artifact_download_attempt_sources AS link
  ON link.download_attempt_id=attempt.download_attempt_id
JOIN artifact_sources AS source
  ON source.artifact_source_id=link.artifact_source_id;
