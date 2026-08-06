-- D04 page-evidenced credit-rating document and action normalization.

CREATE TABLE IF NOT EXISTS credit_rating_normalizations (
    normalization_id INTEGER PRIMARY KEY,
    artifact_id INTEGER NOT NULL REFERENCES document_artifacts(artifact_id),
    content_id INTEGER NOT NULL REFERENCES artifact_contents(content_id),
    extraction_id INTEGER NOT NULL REFERENCES document_extractions(extraction_id),
    parser_name TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    options_json TEXT NOT NULL,
    document_class TEXT NOT NULL CHECK (
      document_class IN (
        'rating_rationale', 'rating_letter', 'surveillance',
        'reaffirmation', 'unknown'
      )
    ),
    agency_raw TEXT,
    agency_code TEXT,
    action_date_raw TEXT,
    action_date TEXT,
    normalization_status TEXT NOT NULL CHECK (
      normalization_status IN ('accepted', 'manual_review', 'failed')
    ),
    accepted_action_count INTEGER NOT NULL CHECK (accepted_action_count >= 0),
    review_action_count INTEGER NOT NULL CHECK (review_action_count >= 0),
    conflict_action_count INTEGER NOT NULL CHECK (conflict_action_count >= 0),
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT NOT NULL,
    error_json TEXT NOT NULL DEFAULT '{}',
    created_at_utc TEXT NOT NULL,
    UNIQUE(extraction_id, parser_name, parser_version, options_json)
);

CREATE TABLE IF NOT EXISTS credit_rating_source_tables (
    source_table_id INTEGER PRIMARY KEY,
    normalization_id INTEGER NOT NULL
      REFERENCES credit_rating_normalizations(normalization_id),
    extraction_id INTEGER NOT NULL,
    page_number INTEGER NOT NULL CHECK (page_number >= 1),
    element_position INTEGER NOT NULL CHECK (element_position >= 0),
    source_rows_json TEXT NOT NULL,
    header_mapping_json TEXT NOT NULL,
    bbox_json TEXT,
    table_status TEXT NOT NULL CHECK (
      table_status IN ('normalized', 'ambiguous', 'unrelated')
    ),
    review_reason_json TEXT NOT NULL DEFAULT '[]',
    UNIQUE(normalization_id, page_number, element_position),
    FOREIGN KEY(extraction_id, page_number)
      REFERENCES document_pages(extraction_id, page_or_slide_number)
);

CREATE TABLE IF NOT EXISTS credit_rating_passages (
    normalization_id INTEGER NOT NULL
      REFERENCES credit_rating_normalizations(normalization_id),
    extraction_id INTEGER NOT NULL,
    page_number INTEGER NOT NULL CHECK (page_number >= 1),
    passage_position INTEGER NOT NULL CHECK (passage_position >= 0),
    element_position INTEGER NOT NULL CHECK (element_position >= 0),
    passage_kind TEXT NOT NULL CHECK (
      passage_kind IN (
        'rationale', 'rating_history', 'document_heading', 'agency_header',
        'action_date', 'context'
      )
    ),
    text_raw TEXT NOT NULL,
    text_origin TEXT NOT NULL CHECK (text_origin IN ('native', 'ocr')),
    bbox_json TEXT,
    PRIMARY KEY(normalization_id, passage_position),
    UNIQUE(normalization_id, page_number, element_position),
    FOREIGN KEY(extraction_id, page_number)
      REFERENCES document_pages(extraction_id, page_or_slide_number)
);

CREATE TABLE IF NOT EXISTS credit_rating_actions (
    rating_action_id INTEGER PRIMARY KEY,
    normalization_id INTEGER NOT NULL
      REFERENCES credit_rating_normalizations(normalization_id),
    source_table_id INTEGER NOT NULL
      REFERENCES credit_rating_source_tables(source_table_id),
    source_row_position INTEGER NOT NULL CHECK (source_row_position >= 1),
    facility_or_instrument_raw TEXT NOT NULL,
    facility_or_instrument_code TEXT,
    amount_display TEXT,
    amount_decimal TEXT,
    currency TEXT,
    unit_code TEXT,
    scale_multiplier TEXT,
    rating_raw TEXT NOT NULL,
    rating_code TEXT,
    outlook_raw TEXT,
    outlook_code TEXT,
    rating_action_raw TEXT NOT NULL,
    rating_action_code TEXT,
    action_date_raw TEXT,
    action_date TEXT,
    canonical_status TEXT NOT NULL CHECK (
      canonical_status IN ('accepted', 'review', 'conflict')
    ),
    is_canonical INTEGER NOT NULL CHECK (is_canonical IN (0, 1)),
    source_row_json TEXT NOT NULL,
    ambiguity_json TEXT NOT NULL DEFAULT '[]',
    CHECK (
      (canonical_status='accepted' AND is_canonical=1)
      OR (canonical_status IN ('review', 'conflict') AND is_canonical=0)
    ),
    UNIQUE(normalization_id, source_table_id, source_row_position)
);

CREATE TABLE IF NOT EXISTS credit_rating_field_evidence (
    evidence_id INTEGER PRIMARY KEY,
    normalization_id INTEGER NOT NULL
      REFERENCES credit_rating_normalizations(normalization_id),
    rating_action_id INTEGER REFERENCES credit_rating_actions(rating_action_id),
    extraction_id INTEGER NOT NULL,
    source_table_id INTEGER REFERENCES credit_rating_source_tables(source_table_id),
    field_code TEXT NOT NULL CHECK (
      field_code IN (
        'document_class', 'agency', 'facility_or_instrument', 'amount',
        'rating', 'outlook', 'rating_action', 'action_date'
      )
    ),
    page_number INTEGER NOT NULL CHECK (page_number >= 1),
    element_position INTEGER,
    source_row_position INTEGER,
    source_column_position INTEGER,
    source_value_raw TEXT NOT NULL,
    text_origin TEXT NOT NULL CHECK (text_origin IN ('native', 'ocr')),
    evidence_kind TEXT NOT NULL CHECK (
      evidence_kind IN ('page_text', 'table_cell')
    ),
    bbox_json TEXT,
    FOREIGN KEY(extraction_id, page_number)
      REFERENCES document_pages(extraction_id, page_or_slide_number),
    UNIQUE(
      normalization_id, rating_action_id, field_code, page_number,
      element_position, source_row_position, source_column_position
    )
);

CREATE TABLE IF NOT EXISTS credit_rating_normalization_validations (
    normalization_id INTEGER NOT NULL
      REFERENCES credit_rating_normalizations(normalization_id),
    rule_code TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (
      severity IN ('error', 'warning', 'manual_review')
    ),
    status TEXT NOT NULL CHECK (status IN ('passed', 'failed')),
    expected_value TEXT,
    actual_value TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(normalization_id, rule_code)
);

CREATE TABLE IF NOT EXISTS preferred_credit_rating_normalizations (
    artifact_id INTEGER PRIMARY KEY REFERENCES document_artifacts(artifact_id),
    normalization_id INTEGER NOT NULL UNIQUE
      REFERENCES credit_rating_normalizations(normalization_id),
    updated_at_utc TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_credit_rating_normalizations_artifact
ON credit_rating_normalizations(artifact_id, normalization_id);

CREATE INDEX IF NOT EXISTS idx_credit_rating_actions_date
ON credit_rating_actions(action_date, facility_or_instrument_code);

CREATE INDEX IF NOT EXISTS idx_credit_rating_evidence_action
ON credit_rating_field_evidence(rating_action_id, field_code);

CREATE TRIGGER IF NOT EXISTS credit_rating_normalization_identity_insert_guard
BEFORE INSERT ON credit_rating_normalizations
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1
    FROM document_extractions AS extraction
    JOIN artifact_contents AS content USING (content_id)
    WHERE extraction.extraction_id=NEW.extraction_id
      AND extraction.content_id=NEW.content_id
      AND content.artifact_id=NEW.artifact_id
      AND extraction.extraction_status='ok'
  ) THEN RAISE(ABORT, 'credit rating normalization identity mismatch') END;
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM document_artifacts
    WHERE artifact_id=NEW.artifact_id AND artifact_type='credit_rating'
  ) THEN RAISE(ABORT, 'credit rating normalization requires credit_rating artifact') END;
END;

CREATE TRIGGER IF NOT EXISTS credit_rating_source_table_identity_guard
BEFORE INSERT ON credit_rating_source_tables
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM credit_rating_normalizations
    WHERE normalization_id=NEW.normalization_id
      AND extraction_id=NEW.extraction_id
  ) THEN RAISE(ABORT, 'credit rating table extraction mismatch') END;
END;

CREATE TRIGGER IF NOT EXISTS credit_rating_evidence_identity_guard
BEFORE INSERT ON credit_rating_field_evidence
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM credit_rating_normalizations
    WHERE normalization_id=NEW.normalization_id
      AND extraction_id=NEW.extraction_id
  ) THEN RAISE(ABORT, 'credit rating evidence extraction mismatch') END;
  SELECT CASE WHEN NEW.rating_action_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM credit_rating_actions
    WHERE rating_action_id=NEW.rating_action_id
      AND normalization_id=NEW.normalization_id
  ) THEN RAISE(ABORT, 'credit rating evidence action mismatch') END;
END;

CREATE TRIGGER IF NOT EXISTS preferred_credit_rating_insert_guard
BEFORE INSERT ON preferred_credit_rating_normalizations
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM credit_rating_normalizations
    WHERE normalization_id=NEW.normalization_id
      AND artifact_id=NEW.artifact_id
      AND normalization_status='accepted'
  ) THEN RAISE(ABORT, 'preferred credit rating normalization must be accepted') END;
END;

CREATE TRIGGER IF NOT EXISTS preferred_credit_rating_update_guard
BEFORE UPDATE OF normalization_id ON preferred_credit_rating_normalizations
BEGIN
  SELECT CASE WHEN NOT EXISTS (
    SELECT 1 FROM credit_rating_normalizations
    WHERE normalization_id=NEW.normalization_id
      AND artifact_id=NEW.artifact_id
      AND normalization_status='accepted'
  ) THEN RAISE(ABORT, 'preferred credit rating normalization must be accepted') END;
END;

CREATE VIEW IF NOT EXISTS current_credit_rating_actions AS
SELECT action.*, normalization.artifact_id, normalization.content_id,
       normalization.extraction_id, normalization.document_class,
       normalization.agency_raw, normalization.agency_code
FROM preferred_credit_rating_normalizations AS preferred
JOIN credit_rating_normalizations AS normalization
  ON normalization.normalization_id=preferred.normalization_id
JOIN credit_rating_actions AS action
  ON action.normalization_id=normalization.normalization_id
WHERE action.is_canonical=1;
