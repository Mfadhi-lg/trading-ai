-- Research migration SS-01 v1. Deliberately outside the live migration directory.
CREATE TABLE IF NOT EXISTS ss_migrations(version TEXT PRIMARY KEY, sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ss_sources(
 source_id TEXT PRIMARY KEY, source_path TEXT NOT NULL, sha256 TEXT NOT NULL,
 format TEXT NOT NULL, quarter TEXT NOT NULL, period_end TEXT NOT NULL,
 known_at TEXT, date_basis TEXT NOT NULL, content BLOB NOT NULL,
 UNIQUE(source_path,sha256)
);
CREATE TABLE IF NOT EXISTS ss_rows(
 row_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES ss_sources(source_id),
 row_ref TEXT NOT NULL, raw_text TEXT NOT NULL, symbol_raw TEXT NOT NULL,
 exchange_raw TEXT, company_name_raw TEXT, label_raw TEXT,
 company_id INTEGER REFERENCES companies(company_id), resolution_state TEXT NOT NULL
 CHECK(resolution_state IN ('resolved','unresolved','ambiguous')),
 resolution_json TEXT NOT NULL, UNIQUE(source_id,row_ref),
 CHECK((resolution_state='resolved') = (company_id IS NOT NULL))
);
-- Source categories are not silently promoted into a finer taxonomy.
CREATE TABLE IF NOT EXISTS ss_subsectors(
 subsector_id TEXT PRIMARY KEY, label_raw TEXT NOT NULL,
 label_normalized TEXT NOT NULL, label_kind TEXT NOT NULL DEFAULT 'source_category',
 sector_id INTEGER, UNIQUE(label_kind,label_raw)
);
CREATE TABLE IF NOT EXISTS ss_memberships(
 company_id INTEGER NOT NULL REFERENCES companies(company_id),
 quarter TEXT NOT NULL, PRIMARY KEY(company_id,quarter)
);
CREATE TABLE IF NOT EXISTS ss_membership_evidence(
 row_id TEXT PRIMARY KEY REFERENCES ss_rows(row_id),
 company_id INTEGER NOT NULL, quarter TEXT NOT NULL,
 FOREIGN KEY(company_id,quarter) REFERENCES ss_memberships(company_id,quarter)
);
CREATE TABLE IF NOT EXISTS ss_assignments(
 assignment_id TEXT PRIMARY KEY,
 source_id TEXT NOT NULL REFERENCES ss_sources(source_id),
 company_id INTEGER NOT NULL REFERENCES companies(company_id),
 subsector_id TEXT NOT NULL REFERENCES ss_subsectors(subsector_id),
 known_at TEXT, valid_from TEXT, valid_to TEXT,
 UNIQUE(source_id,company_id,subsector_id)
);
CREATE TABLE IF NOT EXISTS ss_assignment_evidence(
 row_id TEXT PRIMARY KEY REFERENCES ss_rows(row_id),
 assignment_id TEXT NOT NULL REFERENCES ss_assignments(assignment_id)
);
CREATE TABLE IF NOT EXISTS ss_mentor_evidence(
 row_id TEXT PRIMARY KEY REFERENCES ss_rows(row_id),
 decision TEXT NOT NULL CHECK(decision IN ('focus_reference','unknown')),
 known_at TEXT
);
CREATE VIEW IF NOT EXISTS ss_classification_evidence AS
 SELECT r.*,s.source_path,s.sha256 source_sha256,s.quarter,s.period_end,
 a.subsector_id,d.label_normalized,d.label_kind,a.known_at,a.valid_from,a.valid_to,
 m.decision
 FROM ss_rows r JOIN ss_sources s USING(source_id)
 LEFT JOIN ss_assignment_evidence ae USING(row_id)
 LEFT JOIN ss_assignments a USING(assignment_id)
 LEFT JOIN ss_subsectors d USING(subsector_id)
 LEFT JOIN ss_mentor_evidence m USING(row_id);
