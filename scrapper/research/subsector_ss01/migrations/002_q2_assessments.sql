-- SS-01 additive Q2 foundation. Migration 001 and its historical rows are immutable.
CREATE TABLE IF NOT EXISTS ss_focus_entries(
 entry_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES ss_sources(source_id),
 quarter TEXT NOT NULL, company_id INTEGER REFERENCES companies(company_id),
 identity_key TEXT NOT NULL, display_stock_raw TEXT NOT NULL,
 assessment_state TEXT NOT NULL CHECK(assessment_state IN ('single','consistent_duplicate','conflicting_duplicate')),
 UNIQUE(source_id,identity_key), FOREIGN KEY(company_id,quarter) REFERENCES ss_memberships(company_id,quarter)
);
CREATE TABLE IF NOT EXISTS ss_focus_entry_rows(
 row_id TEXT PRIMARY KEY REFERENCES ss_rows(row_id),
 entry_id TEXT NOT NULL REFERENCES ss_focus_entries(entry_id)
);
CREATE TABLE IF NOT EXISTS ss_mentor_assessments(
 row_id TEXT PRIMARY KEY REFERENCES ss_rows(row_id),
 company_id INTEGER REFERENCES companies(company_id), quarter TEXT NOT NULL,
 original_row_number INTEGER NOT NULL, sl_no_raw TEXT NOT NULL, stock_raw TEXT NOT NULL,
 q2_results_raw TEXT NOT NULL, rating_raw TEXT NOT NULL, guidance_raw TEXT NOT NULL,
 extraction_note_raw TEXT NOT NULL,
 decision_at TEXT, available_at TEXT,
 FOREIGN KEY(company_id,quarter) REFERENCES ss_memberships(company_id,quarter)
);
CREATE TABLE IF NOT EXISTS ss_q2_subsector_status(
 entry_id TEXT PRIMARY KEY REFERENCES ss_focus_entries(entry_id),
 state TEXT NOT NULL CHECK(state IN ('verified_prior_assignment','conflicting_prior_assignments','missing_prior_assignment','identity_review')),
 subsector_id TEXT REFERENCES ss_subsectors(subsector_id),
 known_at TEXT,
 CHECK((state='verified_prior_assignment')=(subsector_id IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS ss_inherited_subsector_evidence(
 entry_id TEXT NOT NULL REFERENCES ss_focus_entries(entry_id),
 prior_assignment_id TEXT NOT NULL REFERENCES ss_assignments(assignment_id),
 PRIMARY KEY(entry_id,prior_assignment_id)
);
CREATE TABLE IF NOT EXISTS ss_import_batches(
 source_id TEXT PRIMARY KEY REFERENCES ss_sources(source_id), plan_sha256 TEXT NOT NULL,
 recorded_at_utc TEXT NOT NULL, decision_at TEXT,
 duplicate_policy TEXT NOT NULL CHECK(duplicate_policy='one_entry_conflicts_require_review')
);
-- Empty future Document Intelligence foundation. Never populated from mentor text.
CREATE TABLE IF NOT EXISTS ss_documents(
 document_id TEXT PRIMARY KEY, company_id INTEGER NOT NULL REFERENCES companies(company_id),
 document_kind TEXT NOT NULL CHECK(document_kind IN ('financial_results','credit_rating_report','investor_presentation','earnings_call_transcript','management_guidance')),
 source_uri TEXT NOT NULL, source_sha256 TEXT NOT NULL, source_version TEXT NOT NULL,
 period_end TEXT, published_at TEXT, available_at TEXT, recorded_at_utc TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ss_extracted_facts(
 fact_id TEXT PRIMARY KEY, document_id TEXT NOT NULL REFERENCES ss_documents(document_id),
 fact_kind TEXT NOT NULL CHECK(fact_kind IN ('financial_metric','agency_rating','management_guidance','other')),
 metric_key TEXT NOT NULL, value_raw TEXT NOT NULL, value_decimal TEXT, unit_raw TEXT,
 agency_raw TEXT, period_end TEXT, available_at TEXT, recorded_at_utc TEXT NOT NULL,
 verification_state TEXT NOT NULL CHECK(verification_state IN ('unverified','verified','disputed')),
 CHECK(fact_kind!='agency_rating' OR (agency_raw IS NOT NULL AND length(agency_raw)>0))
);
CREATE TABLE IF NOT EXISTS ss_fact_citations(
 citation_id TEXT PRIMARY KEY, fact_id TEXT NOT NULL REFERENCES ss_extracted_facts(fact_id),
 source_locator TEXT NOT NULL, quote_raw TEXT NOT NULL, evidence_sha256 TEXT NOT NULL,
 available_at TEXT
);
CREATE TABLE IF NOT EXISTS ss_assessment_fact_links(
 row_id TEXT NOT NULL REFERENCES ss_mentor_assessments(row_id),
 fact_id TEXT NOT NULL REFERENCES ss_extracted_facts(fact_id),
 relationship TEXT NOT NULL CHECK(relationship IN ('supports','contradicts','context')),
 PRIMARY KEY(row_id,fact_id)
);
-- Exactly one operational company entry; conflicting duplicates have no selected judgment.
CREATE VIEW IF NOT EXISTS ss_q2_focus AS
 SELECT e.*,s.source_path,s.sha256 source_sha256,s.source_id source_snapshot_version,
 count(a.row_id) original_row_count,
 CASE WHEN e.assessment_state!='conflicting_duplicate' THEN min(a.q2_results_raw) END q2_results_raw,
 CASE WHEN e.assessment_state!='conflicting_duplicate' THEN min(a.rating_raw) END rating_raw,
 CASE WHEN e.assessment_state!='conflicting_duplicate' THEN min(a.guidance_raw) END guidance_raw,
 p.state subsector_state,d.label_raw subsector_raw,p.known_at subsector_known_at,
 CASE WHEN count(a.decision_at)=count(*) THEN max(a.decision_at) END decision_at,
 CASE WHEN count(a.available_at)=count(*) THEN max(a.available_at) END assessment_available_at
 FROM ss_focus_entries e JOIN ss_sources s USING(source_id)
 JOIN ss_focus_entry_rows er USING(entry_id) JOIN ss_mentor_assessments a USING(row_id)
 JOIN ss_q2_subsector_status p USING(entry_id)
 LEFT JOIN ss_subsectors d USING(subsector_id)
 GROUP BY e.entry_id;
CREATE VIEW IF NOT EXISTS ss_q2_assessment_evidence AS
 SELECT a.*,e.entry_id,e.assessment_state,r.row_ref,r.raw_text,r.resolution_state,r.resolution_json,
 s.source_path,s.sha256 source_sha256,s.source_id source_snapshot_version
 FROM ss_mentor_assessments a JOIN ss_rows r USING(row_id) JOIN ss_sources s USING(source_id)
 JOIN ss_focus_entry_rows er USING(row_id) JOIN ss_focus_entries e USING(entry_id);
