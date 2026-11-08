-- Portable PDF derivatives for validated publisher HTML documents.
-- The original HTML remains publisher_original and is never replaced.

PRAGMA foreign_keys=OFF;
PRAGMA legacy_alter_table=ON;

BEGIN;

ALTER TABLE artifact_content_relationships RENAME TO artifact_content_relationships_before_html_pdf;

CREATE TABLE artifact_content_relationships (
    relationship_id INTEGER PRIMARY KEY,
    artifact_id INTEGER NOT NULL REFERENCES document_artifacts(artifact_id),
    source_content_id INTEGER NOT NULL REFERENCES artifact_contents(content_id),
    derived_content_id INTEGER NOT NULL UNIQUE REFERENCES artifact_contents(content_id),
    relationship_type TEXT NOT NULL CHECK (
      relationship_type IN ('normalized_audio', 'rendered_pdf')
    ),
    created_at_utc TEXT NOT NULL,
    CHECK (source_content_id <> derived_content_id),
    UNIQUE(source_content_id, relationship_type)
);

INSERT INTO artifact_content_relationships
    (relationship_id, artifact_id, source_content_id, derived_content_id,
     relationship_type, created_at_utc)
SELECT relationship_id, artifact_id, source_content_id, derived_content_id,
       relationship_type, created_at_utc
  FROM artifact_content_relationships_before_html_pdf;

DROP TABLE artifact_content_relationships_before_html_pdf;

CREATE TABLE html_document_renderings (
    rendering_id INTEGER PRIMARY KEY,
    artifact_id INTEGER NOT NULL REFERENCES document_artifacts(artifact_id),
    source_content_id INTEGER NOT NULL REFERENCES artifact_contents(content_id),
    rendered_content_id INTEGER NOT NULL UNIQUE REFERENCES artifact_contents(content_id),
    renderer_name TEXT NOT NULL,
    renderer_version TEXT NOT NULL,
    options_json TEXT NOT NULL,
    visible_character_count INTEGER NOT NULL CHECK (visible_character_count > 0),
    block_count INTEGER NOT NULL CHECK (block_count > 0),
    created_at_utc TEXT NOT NULL,
    UNIQUE(source_content_id, renderer_name, renderer_version, options_json)
);

CREATE INDEX idx_html_document_renderings_artifact
ON html_document_renderings(artifact_id, rendering_id);

COMMIT;

PRAGMA legacy_alter_table=OFF;
PRAGMA foreign_keys=ON;
