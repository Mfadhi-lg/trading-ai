-- D07 gated, auditable recording-to-transcript fallback.

ALTER TABLE transcript_segments
ADD COLUMN uncertainty_json TEXT NOT NULL DEFAULT '{}';

CREATE TABLE IF NOT EXISTS machine_transcription_runs (
    transcription_run_id INTEGER PRIMARY KEY,
    transcript_artifact_id INTEGER NOT NULL
      REFERENCES document_artifacts(artifact_id),
    recording_artifact_id INTEGER NOT NULL
      REFERENCES document_artifacts(artifact_id),
    normalization_id INTEGER NOT NULL
      REFERENCES recording_normalizations(normalization_id),
    source_media_content_id INTEGER NOT NULL
      REFERENCES artifact_contents(content_id),
    source_media_sha256 TEXT NOT NULL,
    source_media_duration_ms INTEGER NOT NULL CHECK (source_media_duration_ms > 0),
    source_recording_content_id INTEGER NOT NULL
      REFERENCES artifact_contents(content_id),
    source_recording_sha256 TEXT NOT NULL,
    engine TEXT NOT NULL,
    engine_version TEXT NOT NULL,
    model_version TEXT NOT NULL,
    options_json TEXT NOT NULL,
    comparison_requested INTEGER NOT NULL DEFAULT 0 CHECK (
      comparison_requested IN (0, 1)
    ),
    gate_reason TEXT NOT NULL CHECK (
      gate_reason IN ('no_usable_official', 'human_comparison')
    ),
    detected_language TEXT,
    language_confidence TEXT,
    duration_coverage TEXT,
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT NOT NULL,
    run_status TEXT NOT NULL CHECK (
      run_status IN ('accepted', 'manual_review', 'failed')
    ),
    generated_content_id INTEGER REFERENCES artifact_contents(content_id),
    transcript_id INTEGER UNIQUE REFERENCES transcripts(transcript_id),
    error_json TEXT NOT NULL DEFAULT '{}',
    CHECK (
      (run_status IN ('accepted', 'manual_review') AND transcript_id IS NOT NULL)
      OR run_status='failed'
    ),
    UNIQUE(
      source_media_content_id, engine, engine_version, model_version, options_json
    )
);

CREATE INDEX IF NOT EXISTS idx_machine_transcription_target
ON machine_transcription_runs(transcript_artifact_id, transcription_run_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_machine_transcript_source_identity
ON transcripts(source_media_content_id, engine, model_version, options_json)
WHERE origin='machine_generated';

CREATE TRIGGER IF NOT EXISTS machine_transcription_run_insert_guard
BEFORE INSERT ON machine_transcription_runs
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM document_artifacts
    WHERE artifact_id=NEW.transcript_artifact_id AND artifact_type='transcript'
  ) THEN RAISE(ABORT, 'machine transcription target must be transcript artifact') END;
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM document_artifacts
    WHERE artifact_id=NEW.recording_artifact_id AND artifact_type='recording'
  ) THEN RAISE(ABORT, 'machine transcription source must be recording artifact') END;
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1
    FROM document_artifacts AS transcript_artifact
    JOIN document_artifacts AS recording_artifact
      ON recording_artifact.artifact_id=NEW.recording_artifact_id
    WHERE transcript_artifact.artifact_id=NEW.transcript_artifact_id
      AND transcript_artifact.company_id=recording_artifact.company_id
      AND (
        (transcript_artifact.event_date IS NOT NULL
         AND transcript_artifact.event_date=recording_artifact.event_date)
        OR
        (transcript_artifact.period_end IS NOT NULL
         AND transcript_artifact.period_end=recording_artifact.period_end)
        OR
        (transcript_artifact.fiscal_year IS NOT NULL
         AND transcript_artifact.fiscal_year=recording_artifact.fiscal_year
         AND transcript_artifact.fiscal_quarter IS NOT NULL
         AND transcript_artifact.fiscal_quarter=recording_artifact.fiscal_quarter)
      )
  ) THEN RAISE(ABORT, 'recording and transcript event identity mismatch') END;
  SELECT CASE WHEN EXISTS (
    SELECT 1
    FROM document_artifacts AS transcript_artifact
    JOIN document_artifacts AS recording_artifact
      ON recording_artifact.artifact_id=NEW.recording_artifact_id
    WHERE transcript_artifact.artifact_id=NEW.transcript_artifact_id
      AND (
        (transcript_artifact.event_date IS NOT NULL
         AND recording_artifact.event_date IS NOT NULL
         AND transcript_artifact.event_date<>recording_artifact.event_date)
        OR
        (transcript_artifact.period_end IS NOT NULL
         AND recording_artifact.period_end IS NOT NULL
         AND transcript_artifact.period_end<>recording_artifact.period_end)
        OR
        (transcript_artifact.fiscal_year IS NOT NULL
         AND recording_artifact.fiscal_year IS NOT NULL
         AND transcript_artifact.fiscal_year<>recording_artifact.fiscal_year)
        OR
        (transcript_artifact.fiscal_quarter IS NOT NULL
         AND recording_artifact.fiscal_quarter IS NOT NULL
         AND transcript_artifact.fiscal_quarter<>recording_artifact.fiscal_quarter)
      )
  ) THEN RAISE(ABORT, 'recording and transcript event identity conflict') END;
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1
    FROM recording_normalizations AS normalization
    JOIN artifact_contents AS media
      ON media.content_id=normalization.normalized_content_id
    JOIN artifact_contents AS source
      ON source.content_id=normalization.source_content_id
    WHERE normalization.normalization_id=NEW.normalization_id
      AND normalization.artifact_id=NEW.recording_artifact_id
      AND normalization.normalization_status='accepted'
      AND normalization.normalized_content_id=NEW.source_media_content_id
      AND normalization.source_content_id=NEW.source_recording_content_id
      AND normalization.normalized_duration_ms=NEW.source_media_duration_ms
      AND media.content_sha256=NEW.source_media_sha256
      AND source.content_sha256=NEW.source_recording_sha256
  ) THEN RAISE(ABORT, 'machine transcription requires accepted normalized audio') END;
  SELECT CASE WHEN NEW.transcript_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM transcripts
    WHERE transcript_id=NEW.transcript_id
      AND artifact_id=NEW.transcript_artifact_id
      AND origin='machine_generated'
      AND source_media_content_id=NEW.source_media_content_id
      AND engine=NEW.engine || '@' || NEW.engine_version
      AND model_version=NEW.model_version
      AND options_json=NEW.options_json
      AND validation_status=NEW.run_status
  ) THEN RAISE(ABORT, 'machine transcription transcript identity mismatch') END;
  SELECT CASE WHEN NEW.generated_content_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM artifact_contents
    WHERE content_id=NEW.generated_content_id
      AND artifact_id=NEW.transcript_artifact_id
      AND is_official=0 AND content_origin='generated_derivative'
  ) THEN RAISE(ABORT, 'machine transcription generated content mismatch') END;
  SELECT CASE WHEN NEW.transcript_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM transcripts
    WHERE transcript_id=NEW.transcript_id
      AND content_id=NEW.generated_content_id
  ) THEN RAISE(ABORT, 'machine transcript content mismatch') END;
  SELECT CASE WHEN NEW.comparison_requested=0 AND EXISTS (
    SELECT 1 FROM transcripts
    WHERE artifact_id=NEW.transcript_artifact_id
      AND origin='official' AND validation_status='accepted'
  ) THEN RAISE(ABORT, 'usable official transcript blocks machine transcription') END;
END;

CREATE VIEW IF NOT EXISTS current_machine_transcription_provenance AS
SELECT run.*, transcript.validation_status AS transcript_validation_status
FROM machine_transcription_runs AS run
LEFT JOIN transcripts AS transcript USING (transcript_id);
