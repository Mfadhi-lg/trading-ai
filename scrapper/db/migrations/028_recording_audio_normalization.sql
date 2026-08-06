-- D06 official recording resolution and deterministic audio normalization.

CREATE TABLE IF NOT EXISTS recording_normalizations (
    normalization_id INTEGER PRIMARY KEY,
    artifact_id INTEGER NOT NULL REFERENCES document_artifacts(artifact_id),
    source_content_id INTEGER NOT NULL REFERENCES artifact_contents(content_id),
    normalized_content_id INTEGER UNIQUE REFERENCES artifact_contents(content_id),
    resolution_basis TEXT NOT NULL CHECK (
      resolution_basis='primary_source_validated_download'
    ),
    probe_engine TEXT NOT NULL,
    probe_engine_version TEXT NOT NULL,
    normalizer_engine TEXT NOT NULL,
    normalizer_engine_version TEXT NOT NULL,
    options_json TEXT NOT NULL,
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT NOT NULL,
    normalization_status TEXT NOT NULL CHECK (
      normalization_status IN ('accepted', 'failed')
    ),
    source_duration_ms INTEGER CHECK (
      source_duration_ms IS NULL OR source_duration_ms >= 0
    ),
    source_container_formats_json TEXT NOT NULL DEFAULT '[]',
    source_streams_json TEXT NOT NULL DEFAULT '[]',
    normalized_duration_ms INTEGER CHECK (
      normalized_duration_ms IS NULL OR normalized_duration_ms >= 0
    ),
    normalized_container_formats_json TEXT NOT NULL DEFAULT '[]',
    normalized_streams_json TEXT NOT NULL DEFAULT '[]',
    error_json TEXT NOT NULL DEFAULT '{}',
    CHECK (
      (normalization_status='accepted'
       AND normalized_content_id IS NOT NULL
       AND source_duration_ms IS NOT NULL
       AND normalized_duration_ms IS NOT NULL)
      OR
      (normalization_status='failed' AND normalized_content_id IS NULL)
    ),
    UNIQUE(
      source_content_id, probe_engine, probe_engine_version,
      normalizer_engine, normalizer_engine_version, options_json
    )
);

CREATE TABLE IF NOT EXISTS artifact_content_relationships (
    relationship_id INTEGER PRIMARY KEY,
    artifact_id INTEGER NOT NULL REFERENCES document_artifacts(artifact_id),
    source_content_id INTEGER NOT NULL REFERENCES artifact_contents(content_id),
    derived_content_id INTEGER NOT NULL UNIQUE
      REFERENCES artifact_contents(content_id),
    relationship_type TEXT NOT NULL CHECK (
      relationship_type='normalized_audio'
    ),
    created_at_utc TEXT NOT NULL,
    CHECK (source_content_id <> derived_content_id),
    UNIQUE(source_content_id, relationship_type)
);

CREATE TABLE IF NOT EXISTS recording_normalization_validations (
    normalization_id INTEGER NOT NULL
      REFERENCES recording_normalizations(normalization_id),
    rule_code TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('error', 'warning')),
    status TEXT NOT NULL CHECK (status IN ('passed', 'failed')),
    expected_value TEXT,
    actual_value TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(normalization_id, rule_code)
);

CREATE INDEX IF NOT EXISTS idx_recording_normalizations_artifact
ON recording_normalizations(artifact_id, normalization_id);

CREATE INDEX IF NOT EXISTS idx_recording_normalizations_source
ON recording_normalizations(source_content_id, normalization_status);

CREATE TRIGGER IF NOT EXISTS recording_normalization_identity_insert_guard
BEFORE INSERT ON recording_normalizations
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM document_artifacts
    WHERE artifact_id=NEW.artifact_id AND artifact_type='recording'
  ) THEN RAISE(ABORT, 'recording normalization requires recording artifact') END;
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM artifact_contents
    WHERE content_id=NEW.source_content_id
      AND artifact_id=NEW.artifact_id
      AND is_official=1
      AND content_origin='publisher_original'
  ) THEN RAISE(ABORT, 'recording source must be official publisher content') END;
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1
    FROM artifact_download_attempts AS attempt
    JOIN artifact_download_attempt_sources AS link USING (download_attempt_id)
    JOIN artifact_sources AS source USING (artifact_source_id)
    WHERE attempt.content_id=NEW.source_content_id
      AND attempt.artifact_id=NEW.artifact_id
      AND attempt.result_status='validated'
      AND source.is_primary_source=1
  ) THEN RAISE(ABORT, 'recording source requires validated primary-source download') END;
  SELECT CASE WHEN NEW.normalized_content_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM artifact_contents
    WHERE content_id=NEW.normalized_content_id
      AND artifact_id=NEW.artifact_id
      AND media_type='audio/wav'
      AND is_official=0
      AND content_origin='generated_derivative'
  ) THEN RAISE(ABORT, 'normalized audio must be a generated WAV derivative') END;
END;

CREATE TRIGGER IF NOT EXISTS artifact_content_relationship_identity_insert_guard
BEFORE INSERT ON artifact_content_relationships
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM document_artifacts
    WHERE artifact_id=NEW.artifact_id AND artifact_type='recording'
  ) THEN RAISE(ABORT, 'audio relationship requires recording artifact') END;
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM artifact_contents
    WHERE content_id=NEW.source_content_id
      AND artifact_id=NEW.artifact_id
      AND content_origin='publisher_original'
  ) THEN RAISE(ABORT, 'audio relationship source mismatch') END;
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM artifact_contents
    WHERE content_id=NEW.derived_content_id
      AND artifact_id=NEW.artifact_id
      AND media_type='audio/wav'
      AND is_official=0
      AND content_origin='generated_derivative'
  ) THEN RAISE(ABORT, 'audio relationship derivative mismatch') END;
END;

CREATE VIEW IF NOT EXISTS accepted_recording_audio AS
SELECT normalization.*, relationship.relationship_id,
       source.content_sha256 AS source_content_sha256,
       source.media_type AS source_media_type,
       derived.content_sha256 AS normalized_content_sha256,
       derived.media_type AS normalized_media_type
FROM recording_normalizations AS normalization
JOIN artifact_content_relationships AS relationship
  ON relationship.source_content_id=normalization.source_content_id
 AND relationship.derived_content_id=normalization.normalized_content_id
 AND relationship.relationship_type='normalized_audio'
JOIN artifact_contents AS source
  ON source.content_id=normalization.source_content_id
JOIN artifact_contents AS derived
  ON derived.content_id=normalization.normalized_content_id
WHERE normalization.normalization_status='accepted';
