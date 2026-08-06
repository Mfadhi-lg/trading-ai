-- D05 official transcript segmentation, page evidence and preference.

CREATE TABLE IF NOT EXISTS transcripts (
    transcript_id INTEGER PRIMARY KEY,
    artifact_id INTEGER NOT NULL REFERENCES document_artifacts(artifact_id),
    content_id INTEGER NOT NULL REFERENCES artifact_contents(content_id),
    extraction_id INTEGER REFERENCES document_extractions(extraction_id),
    origin TEXT NOT NULL CHECK (origin IN ('official', 'machine_generated')),
    source_media_content_id INTEGER REFERENCES artifact_contents(content_id),
    language TEXT NOT NULL DEFAULT 'und',
    engine TEXT NOT NULL,
    model_version TEXT NOT NULL,
    options_json TEXT NOT NULL,
    document_marker_raw TEXT,
    document_marker_page INTEGER,
    created_at_utc TEXT NOT NULL,
    validation_status TEXT NOT NULL CHECK (
      validation_status IN ('accepted', 'manual_review', 'failed')
    ),
    segment_count INTEGER NOT NULL CHECK (segment_count >= 0),
    page_start INTEGER,
    page_end INTEGER,
    prepared_segment_count INTEGER NOT NULL CHECK (prepared_segment_count >= 0),
    qa_segment_count INTEGER NOT NULL CHECK (qa_segment_count >= 0),
    unknown_segment_count INTEGER NOT NULL CHECK (unknown_segment_count >= 0),
    error_json TEXT NOT NULL DEFAULT '{}',
    CHECK (page_start IS NULL OR page_start >= 1),
    CHECK (page_end IS NULL OR page_end >= page_start),
    CHECK (document_marker_page IS NULL OR document_marker_page >= 1),
    CHECK (
      (origin='official' AND extraction_id IS NOT NULL
       AND source_media_content_id IS NULL AND page_start IS NOT NULL
       AND page_end IS NOT NULL)
      OR
      (origin='machine_generated' AND extraction_id IS NULL
       AND source_media_content_id IS NOT NULL AND page_start IS NULL
       AND page_end IS NULL)
    ),
    UNIQUE(content_id, origin, engine, model_version, options_json)
);

CREATE TABLE IF NOT EXISTS transcript_section_boundaries (
    transcript_id INTEGER NOT NULL REFERENCES transcripts(transcript_id),
    boundary_position INTEGER NOT NULL CHECK (boundary_position >= 0),
    section_kind TEXT NOT NULL CHECK (
      section_kind IN ('prepared_remarks', 'qa')
    ),
    page_number INTEGER,
    element_position INTEGER,
    start_ms INTEGER,
    label_raw TEXT NOT NULL,
    text_origin TEXT NOT NULL CHECK (
      text_origin IN ('native', 'ocr', 'machine_generated')
    ),
    bbox_json TEXT,
    PRIMARY KEY(transcript_id, boundary_position),
    CHECK (
      (page_number IS NOT NULL AND page_number >= 1
       AND (element_position IS NULL OR element_position >= 0)
       AND start_ms IS NULL AND text_origin IN ('native', 'ocr'))
      OR
      (page_number IS NULL AND element_position IS NULL
       AND start_ms IS NOT NULL AND start_ms >= 0
       AND text_origin='machine_generated')
    )
);

CREATE TABLE IF NOT EXISTS transcript_segments (
    transcript_id INTEGER NOT NULL REFERENCES transcripts(transcript_id),
    segment_position INTEGER NOT NULL CHECK (segment_position >= 0),
    page_start INTEGER,
    page_end INTEGER,
    element_start INTEGER,
    element_end INTEGER,
    start_ms INTEGER,
    end_ms INTEGER,
    speaker_raw TEXT NOT NULL,
    speaker_normalized TEXT NOT NULL,
    text_raw TEXT NOT NULL,
    confidence TEXT,
    section_kind TEXT NOT NULL CHECK (
      section_kind IN ('prepared_remarks', 'qa', 'unknown')
    ),
    text_origin TEXT NOT NULL CHECK (
      text_origin IN ('native', 'ocr', 'mixed', 'machine_generated')
    ),
    source_fragments_json TEXT NOT NULL,
    PRIMARY KEY(transcript_id, segment_position),
    CHECK (length(trim(text_raw)) > 0),
    CHECK (
      (page_start IS NOT NULL AND page_start >= 1
       AND page_end IS NOT NULL AND page_end >= page_start
       AND ((element_start IS NULL AND element_end IS NULL)
            OR (element_start IS NOT NULL AND element_start >= 0
                AND element_end IS NOT NULL AND element_end >= 0))
       AND start_ms IS NULL AND end_ms IS NULL
       AND text_origin IN ('native', 'ocr', 'mixed'))
      OR
      (page_start IS NULL AND page_end IS NULL
       AND element_start IS NULL AND element_end IS NULL
       AND start_ms IS NOT NULL AND start_ms >= 0
       AND end_ms IS NOT NULL AND end_ms >= start_ms
       AND text_origin='machine_generated')
    )
);

CREATE TABLE IF NOT EXISTS transcript_validations (
    transcript_id INTEGER NOT NULL REFERENCES transcripts(transcript_id),
    rule_code TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (
      severity IN ('error', 'warning', 'manual_review')
    ),
    status TEXT NOT NULL CHECK (status IN ('passed', 'failed')),
    expected_value TEXT,
    actual_value TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(transcript_id, rule_code)
);

CREATE TABLE IF NOT EXISTS preferred_transcripts (
    artifact_id INTEGER PRIMARY KEY REFERENCES document_artifacts(artifact_id),
    transcript_id INTEGER NOT NULL UNIQUE REFERENCES transcripts(transcript_id),
    updated_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_transcripts_artifact_origin
ON transcripts(artifact_id, origin, transcript_id);

CREATE INDEX IF NOT EXISTS idx_transcript_segments_speaker
ON transcript_segments(transcript_id, speaker_normalized, segment_position);

CREATE TRIGGER IF NOT EXISTS transcript_identity_insert_guard
BEFORE INSERT ON transcripts
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM document_artifacts
    WHERE artifact_id=NEW.artifact_id AND artifact_type='transcript'
  ) THEN RAISE(ABORT, 'transcript requires transcript artifact') END;
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM artifact_contents
    WHERE content_id=NEW.content_id AND artifact_id=NEW.artifact_id
  ) THEN RAISE(ABORT, 'transcript content must belong to artifact') END;
  SELECT CASE WHEN NEW.origin='official' AND NOT EXISTS (
    SELECT 1
    FROM artifact_contents AS content
    JOIN document_extractions AS extraction USING (content_id)
    WHERE content.content_id=NEW.content_id
      AND content.is_official=1
      AND content.content_origin='publisher_original'
      AND extraction.extraction_id=NEW.extraction_id
      AND extraction.extraction_status='ok'
  ) THEN RAISE(ABORT, 'official transcript requires usable official extraction') END;
  SELECT CASE WHEN NEW.origin='machine_generated' AND NOT EXISTS (
    SELECT 1 FROM artifact_contents AS content
    WHERE content.content_id=NEW.content_id
      AND content.artifact_id=NEW.artifact_id
      AND content.is_official=0
      AND content.content_origin='generated_derivative'
  ) THEN RAISE(ABORT, 'machine transcript requires generated derivative content') END;
END;

CREATE TRIGGER IF NOT EXISTS transcript_segment_identity_guard
BEFORE INSERT ON transcript_segments
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM transcripts AS transcript
    WHERE transcript.transcript_id=NEW.transcript_id
      AND (
        (transcript.origin='official' AND NEW.page_start IS NOT NULL
         AND NEW.start_ms IS NULL AND NEW.text_origin IN ('native', 'ocr', 'mixed'))
        OR
        (transcript.origin='machine_generated' AND NEW.page_start IS NULL
         AND NEW.start_ms IS NOT NULL AND NEW.text_origin='machine_generated')
      )
  ) THEN RAISE(ABORT, 'transcript segment origin mismatch') END;
END;

CREATE TRIGGER IF NOT EXISTS preferred_transcript_insert_guard
BEFORE INSERT ON preferred_transcripts
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM transcripts
    WHERE transcript_id=NEW.transcript_id
      AND artifact_id=NEW.artifact_id
      AND validation_status='accepted'
  ) THEN RAISE(ABORT, 'preferred transcript must be accepted') END;
  SELECT CASE WHEN EXISTS (
    SELECT 1 FROM transcripts AS candidate
    WHERE candidate.transcript_id=NEW.transcript_id
      AND candidate.origin='machine_generated'
  ) AND EXISTS (
    SELECT 1 FROM transcripts AS official
    WHERE official.artifact_id=NEW.artifact_id
      AND official.origin='official'
      AND official.validation_status='accepted'
  ) THEN RAISE(ABORT, 'machine transcript cannot outrank official transcript') END;
END;

CREATE TRIGGER IF NOT EXISTS preferred_transcript_update_guard
BEFORE UPDATE OF transcript_id ON preferred_transcripts
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM transcripts
    WHERE transcript_id=NEW.transcript_id
      AND artifact_id=NEW.artifact_id
      AND validation_status='accepted'
  ) THEN RAISE(ABORT, 'preferred transcript must be accepted') END;
  SELECT CASE WHEN EXISTS (
    SELECT 1 FROM transcripts AS candidate
    WHERE candidate.transcript_id=NEW.transcript_id
      AND candidate.origin='machine_generated'
  ) AND EXISTS (
    SELECT 1 FROM transcripts AS official
    WHERE official.artifact_id=NEW.artifact_id
      AND official.origin='official'
      AND official.validation_status='accepted'
  ) THEN RAISE(ABORT, 'machine transcript cannot outrank official transcript') END;
END;

CREATE TRIGGER IF NOT EXISTS artifact_transcript_content_preference_guard
BEFORE UPDATE OF preferred_content_id ON document_artifacts
WHEN NEW.artifact_type='transcript'
  AND NEW.preferred_content_id IS NOT NULL
  AND EXISTS (
    SELECT 1 FROM artifact_contents
    WHERE content_id=NEW.preferred_content_id AND is_official=0
  )
  AND EXISTS (
    SELECT 1 FROM transcripts
    WHERE artifact_id=NEW.artifact_id AND origin='official'
      AND validation_status='accepted'
  )
BEGIN
  SELECT RAISE(ABORT, 'machine transcript content cannot outrank official transcript');
END;

CREATE TRIGGER IF NOT EXISTS artifact_transcript_status_preference_guard
BEFORE UPDATE OF transcript_preference_status ON document_artifacts
WHEN NEW.transcript_preference_status='machine_preferred'
  AND EXISTS (
    SELECT 1 FROM transcripts
    WHERE artifact_id=NEW.artifact_id AND origin='official'
      AND validation_status='accepted'
  )
BEGIN
  SELECT RAISE(ABORT, 'machine transcript status cannot outrank official transcript');
END;

CREATE VIEW IF NOT EXISTS current_transcripts AS
SELECT transcript.*
FROM preferred_transcripts AS preferred
JOIN transcripts AS transcript USING (transcript_id);

CREATE VIEW IF NOT EXISTS current_transcript_segments AS
SELECT transcript.artifact_id, transcript.origin, segment.*
FROM current_transcripts AS transcript
JOIN transcript_segments AS segment USING (transcript_id);

CREATE VIEW IF NOT EXISTS official_transcript_fallback_status AS
SELECT artifact.artifact_id, artifact.company_id, artifact.event_date,
       artifact.period_end,
       CASE WHEN transcript.origin='official'
                  AND transcript.validation_status='accepted'
            THEN 0 ELSE 1 END AS recording_transcription_needed,
       transcript.transcript_id AS preferred_transcript_id,
       transcript.origin AS preferred_origin
FROM document_artifacts AS artifact
LEFT JOIN current_transcripts AS transcript USING (artifact_id)
WHERE artifact.artifact_type='transcript';
