-- Durable handoff only. No historic corpus scan or automatic processing.
CREATE TABLE document_processing_inbox (
    content_id INTEGER PRIMARY KEY REFERENCES artifact_contents(content_id),
    saved_at_utc TEXT NOT NULL
);
CREATE TABLE document_processing_work (
    work_id INTEGER PRIMARY KEY,
    content_id INTEGER NOT NULL REFERENCES artifact_contents(content_id),
    parser_name TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    ocr_engine TEXT NOT NULL,
    ocr_engine_version TEXT NOT NULL,
    options_json TEXT NOT NULL,
    resource_key TEXT NOT NULL UNIQUE,
    created_at_utc TEXT NOT NULL,
    extraction_id INTEGER REFERENCES document_extractions(extraction_id),
    UNIQUE(content_id, parser_name, parser_version, ocr_engine, ocr_engine_version, options_json)
);
CREATE TRIGGER document_processing_saved AFTER INSERT ON artifact_contents
BEGIN
    INSERT INTO document_processing_inbox VALUES (NEW.content_id, NEW.created_at_utc);
END;
