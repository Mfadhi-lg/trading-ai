-- Additive SS-02 overlay in the existing SS-01 research DB. Never modify ss_* history.
CREATE TABLE IF NOT EXISTS ss02_migrations(version TEXT PRIMARY KEY,sha256 TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ss02_runs(run_id TEXT PRIMARY KEY,plan_sha256 TEXT NOT NULL,baseline_sha256 TEXT NOT NULL,frozen_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ss02_taxonomy(
 code TEXT PRIMARY KEY,label TEXT NOT NULL UNIQUE,is_granular INTEGER NOT NULL CHECK(is_granular=1),
 origin TEXT NOT NULL,definition_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ss02_business_evidence(
 company_id INTEGER PRIMARY KEY REFERENCES companies(company_id),
 evidence_sha256 TEXT NOT NULL,description_raw TEXT NOT NULL,source_available_at TEXT,
 evidence_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ss02_company_mapping(
 company_id INTEGER PRIMARY KEY REFERENCES companies(company_id),
 primary_code TEXT REFERENCES ss02_taxonomy(code),status TEXT NOT NULL,
 origin TEXT NOT NULL,known_at TEXT NOT NULL,valid_from TEXT,
 tutor_evidence_json TEXT NOT NULL,decision_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ss02_business_exposures(
 company_id INTEGER NOT NULL REFERENCES companies(company_id),code TEXT NOT NULL REFERENCES ss02_taxonomy(code),
 role TEXT NOT NULL CHECK(role IN ('primary','secondary','unranked_activity')),
 evidence_json TEXT NOT NULL,PRIMARY KEY(company_id,code)
);
CREATE TABLE IF NOT EXISTS ss02_identity_overlay(
 row_id TEXT PRIMARY KEY REFERENCES ss_rows(row_id),company_id INTEGER REFERENCES companies(company_id),
 state TEXT NOT NULL,evidence_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ss02_q2_mapping(
 entry_id TEXT PRIMARY KEY REFERENCES ss_focus_entries(entry_id),company_id INTEGER REFERENCES companies(company_id),
 primary_code TEXT REFERENCES ss02_taxonomy(code),state TEXT NOT NULL,evidence_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ss02_tutor_crosswalk(
 label_raw TEXT NOT NULL,code TEXT NOT NULL REFERENCES ss02_taxonomy(code),
 company_count INTEGER NOT NULL,basis TEXT NOT NULL,PRIMARY KEY(label_raw,code)
);
CREATE TABLE IF NOT EXISTS ss02_security_mapping(
 isin TEXT PRIMARY KEY,company_id INTEGER REFERENCES companies(company_id),
 primary_code TEXT REFERENCES ss02_taxonomy(code),state TEXT NOT NULL,security_evidence_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ss02_baskets(
 basket_id TEXT PRIMARY KEY,code TEXT NOT NULL REFERENCES ss02_taxonomy(code),quarter TEXT NOT NULL,
 cutoff_at TEXT NOT NULL,frozen_at TEXT NOT NULL,status TEXT NOT NULL,chart_ready INTEGER NOT NULL CHECK(chart_ready=0),
 policy_sha256 TEXT NOT NULL,UNIQUE(code,quarter)
);
CREATE TABLE IF NOT EXISTS ss02_basket_members(
 basket_id TEXT NOT NULL REFERENCES ss02_baskets(basket_id),company_id INTEGER NOT NULL REFERENCES companies(company_id),
 rank INTEGER NOT NULL,equal_weight REAL NOT NULL,historical_cap_weight REAL NOT NULL,
 selection_evidence_json TEXT NOT NULL,PRIMARY KEY(basket_id,company_id),UNIQUE(basket_id,rank)
);
CREATE TABLE IF NOT EXISTS ss02_basket_candidate_review(
 basket_id TEXT NOT NULL REFERENCES ss02_baskets(basket_id),company_id INTEGER NOT NULL REFERENCES companies(company_id),
 status TEXT NOT NULL,evidence_json TEXT NOT NULL,PRIMARY KEY(basket_id,company_id)
);
CREATE VIEW IF NOT EXISTS ss02_company_subsectors AS
 SELECT c.company_id,c.current_legal_name,m.primary_code,t.label granular_subsector,m.status,m.origin,
 m.known_at,m.valid_from,m.tutor_evidence_json,m.decision_json
 FROM companies c JOIN ss02_company_mapping m USING(company_id)
 LEFT JOIN ss02_taxonomy t ON t.code=m.primary_code;
CREATE VIEW IF NOT EXISTS ss02_q2_assessment_context AS
 SELECT a.*,i.company_id resolved_company_id,i.state identity_state,q.primary_code,
 t.label granular_subsector,q.state mapping_state
 FROM ss_mentor_assessments a JOIN ss02_identity_overlay i USING(row_id)
 JOIN ss_focus_entry_rows er USING(row_id) JOIN ss02_q2_mapping q USING(entry_id)
 LEFT JOIN ss02_taxonomy t ON t.code=q.primary_code;
