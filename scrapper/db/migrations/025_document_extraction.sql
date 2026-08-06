-- D03 deterministic PDF/PowerPoint extraction with page/slide provenance.

CREATE TABLE IF NOT EXISTS document_extractions (
    extraction_id INTEGER PRIMARY KEY,
    content_id INTEGER NOT NULL REFERENCES artifact_contents(content_id),
    parser_name TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    ocr_engine TEXT NOT NULL,
    ocr_engine_version TEXT NOT NULL,
    options_json TEXT NOT NULL,
    started_at_utc TEXT NOT NULL,
    completed_at_utc TEXT NOT NULL,
    extraction_status TEXT NOT NULL CHECK (
      extraction_status IN ('ok', 'partial', 'failed')
    ),
    page_or_slide_count INTEGER NOT NULL CHECK (page_or_slide_count >= 0),
    native_page_count INTEGER NOT NULL CHECK (native_page_count >= 0),
    ocr_page_count INTEGER NOT NULL CHECK (ocr_page_count >= 0),
    unusable_page_count INTEGER NOT NULL CHECK (unusable_page_count >= 0),
    error_json TEXT NOT NULL DEFAULT '{}',
    created_at_utc TEXT NOT NULL,
    UNIQUE(
      content_id, parser_name, parser_version, ocr_engine,
      ocr_engine_version, options_json
    )
);

CREATE TABLE IF NOT EXISTS document_pages (
    extraction_id INTEGER NOT NULL
      REFERENCES document_extractions(extraction_id),
    content_id INTEGER NOT NULL REFERENCES artifact_contents(content_id),
    page_or_slide_number INTEGER NOT NULL CHECK (page_or_slide_number >= 1),
    page_kind TEXT NOT NULL CHECK (page_kind IN ('page', 'slide')),
    title_raw TEXT,
    native_text TEXT NOT NULL DEFAULT '',
    ocr_text TEXT NOT NULL DEFAULT '',
    selected_text TEXT NOT NULL DEFAULT '',
    selected_text_origin TEXT NOT NULL CHECK (
      selected_text_origin IN ('native', 'ocr', 'none')
    ),
    native_text_usable INTEGER NOT NULL CHECK (native_text_usable IN (0, 1)),
    native_unusable_reason TEXT,
    extraction_confidence TEXT,
    speaker_notes_available INTEGER NOT NULL DEFAULT 0 CHECK (
      speaker_notes_available IN (0, 1)
    ),
    speaker_notes_text TEXT,
    layout_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(extraction_id, page_or_slide_number),
    CHECK (
      (selected_text_origin='native' AND native_text_usable=1
       AND selected_text=native_text)
      OR (selected_text_origin='ocr' AND native_text_usable=0
          AND selected_text=ocr_text)
      OR (selected_text_origin='none' AND selected_text='')
    )
);

CREATE TABLE IF NOT EXISTS document_page_elements (
    extraction_id INTEGER NOT NULL,
    page_or_slide_number INTEGER NOT NULL,
    element_position INTEGER NOT NULL CHECK (element_position >= 0),
    element_kind TEXT NOT NULL CHECK (
      element_kind IN ('text_block', 'table', 'image', 'chart')
    ),
    text_origin TEXT NOT NULL CHECK (text_origin IN ('native', 'ocr', 'none')),
    text_raw TEXT,
    title_raw TEXT,
    rows_json TEXT,
    reference_json TEXT,
    bbox_json TEXT,
    PRIMARY KEY(extraction_id, page_or_slide_number, element_position),
    FOREIGN KEY(extraction_id, page_or_slide_number)
      REFERENCES document_pages(extraction_id, page_or_slide_number)
);

CREATE TABLE IF NOT EXISTS document_extraction_validations (
    extraction_id INTEGER NOT NULL REFERENCES document_extractions(extraction_id),
    rule_code TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('error', 'warning')),
    status TEXT NOT NULL CHECK (status IN ('passed', 'failed')),
    expected_value TEXT,
    actual_value TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY(extraction_id, rule_code)
);

CREATE INDEX IF NOT EXISTS idx_document_extractions_content
ON document_extractions(content_id, extraction_id);

CREATE INDEX IF NOT EXISTS idx_document_page_elements_kind
ON document_page_elements(extraction_id, element_kind);
