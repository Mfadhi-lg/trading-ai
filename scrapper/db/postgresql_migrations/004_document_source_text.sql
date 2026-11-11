-- Preserve original document text including U+0000 as strict UTF-8 bytes.
-- SQLite source lineage and public Python string payloads remain unchanged.
ALTER TABLE credit_rating_passages
    ALTER COLUMN text_raw TYPE BYTEA USING convert_to(text_raw, 'UTF8');
ALTER TABLE document_page_elements
    ALTER COLUMN text_raw TYPE BYTEA USING convert_to(text_raw, 'UTF8');
ALTER TABLE document_pages DROP CONSTRAINT document_pages_check;
ALTER TABLE document_pages
    ALTER COLUMN native_text DROP DEFAULT,
    ALTER COLUMN ocr_text DROP DEFAULT,
    ALTER COLUMN selected_text DROP DEFAULT,
    ALTER COLUMN title_raw TYPE BYTEA USING convert_to(title_raw, 'UTF8'),
    ALTER COLUMN native_text TYPE BYTEA USING convert_to(native_text, 'UTF8'),
    ALTER COLUMN ocr_text TYPE BYTEA USING convert_to(ocr_text, 'UTF8'),
    ALTER COLUMN selected_text TYPE BYTEA USING convert_to(selected_text, 'UTF8');
ALTER TABLE document_pages
    ALTER COLUMN native_text SET DEFAULT ''::bytea,
    ALTER COLUMN ocr_text SET DEFAULT ''::bytea,
    ALTER COLUMN selected_text SET DEFAULT ''::bytea;
ALTER TABLE document_pages ADD CONSTRAINT document_pages_check CHECK (
    (selected_text_origin='native' AND native_text_usable=1 AND selected_text=native_text)
    OR (selected_text_origin='ocr' AND native_text_usable=0 AND selected_text=ocr_text)
    OR (selected_text_origin='none' AND selected_text=''::bytea)
);
