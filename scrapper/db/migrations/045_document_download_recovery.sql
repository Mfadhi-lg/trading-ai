-- Document recovery foundation.
--
-- response_blobs is the physical, content-addressed deduplication boundary.
-- artifact_contents is an attachment of those bytes to a logical artifact, so
-- the same immutable blob may legitimately be attached to more than one
-- artifact when two publisher URLs (or two imperfect discovery identities)
-- return identical bytes.

PRAGMA foreign_keys=OFF;
PRAGMA legacy_alter_table=ON;

BEGIN;

ALTER TABLE artifact_contents RENAME TO artifact_contents_before_recovery;

CREATE TABLE artifact_contents (
    content_id INTEGER PRIMARY KEY,
    artifact_id INTEGER NOT NULL REFERENCES document_artifacts(artifact_id),
    blob_id INTEGER NOT NULL REFERENCES response_blobs(blob_id),
    media_type TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
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

INSERT INTO artifact_contents
    (content_id, artifact_id, blob_id, media_type, content_sha256, byte_size,
     extraction_status, is_official, content_origin, created_at_utc)
SELECT content_id, artifact_id, blob_id, media_type, content_sha256, byte_size,
       extraction_status, is_official, content_origin, created_at_utc
  FROM artifact_contents_before_recovery;

DROP TABLE artifact_contents_before_recovery;

CREATE INDEX IF NOT EXISTS idx_artifact_contents_hash
ON artifact_contents(content_sha256, artifact_id);

CREATE TABLE IF NOT EXISTS document_download_recovery_actions (
    recovery_action_id INTEGER PRIMARY KEY,
    artifact_id INTEGER NOT NULL REFERENCES document_artifacts(artifact_id),
    queue_kind TEXT NOT NULL CHECK (queue_kind IN ('generic', 'credit_rating')),
    download_job_id INTEGER,
    action_type TEXT NOT NULL CHECK (
      action_type IN ('REQUEUE', 'REGISTER_REPLACEMENT_URL')
    ),
    reason_code TEXT NOT NULL CHECK (
      reason_code IN (
        'TRANSIENT_SOURCE_FAILURE', 'SOURCE_REPUBLISHED', 'RESOLVER_UPDATED',
        'OPERATOR_VERIFIED', 'DISCOVERY_REFRESH'
      )
    ),
    reason_detail TEXT,
    replaced_artifact_source_id INTEGER REFERENCES artifact_sources(artifact_source_id),
    replacement_artifact_source_id INTEGER REFERENCES artifact_sources(artifact_source_id),
    previous_status TEXT,
    previous_result_status TEXT,
    previous_error TEXT,
    old_url TEXT,
    new_url TEXT,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    applied_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_document_recovery_artifact
ON document_download_recovery_actions(artifact_id, recovery_action_id);

CREATE INDEX IF NOT EXISTS idx_document_recovery_job
ON document_download_recovery_actions(queue_kind, download_job_id);

CREATE TABLE IF NOT EXISTS manual_document_imports (
    manual_import_id INTEGER PRIMARY KEY,
    artifact_id INTEGER NOT NULL REFERENCES document_artifacts(artifact_id),
    artifact_source_id INTEGER NOT NULL REFERENCES artifact_sources(artifact_source_id),
    content_id INTEGER NOT NULL REFERENCES artifact_contents(content_id),
    input_filename TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    byte_size INTEGER NOT NULL CHECK (byte_size > 0),
    detected_media_type TEXT NOT NULL,
    official_assertion INTEGER NOT NULL CHECK (official_assertion IN (0, 1)),
    reason_detail TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    imported_at_utc TEXT NOT NULL,
    UNIQUE(artifact_id, artifact_source_id, content_id)
);

CREATE INDEX IF NOT EXISTS idx_manual_document_import_artifact
ON manual_document_imports(artifact_id, manual_import_id);

COMMIT;

PRAGMA legacy_alter_table=OFF;
PRAGMA foreign_keys=ON;
