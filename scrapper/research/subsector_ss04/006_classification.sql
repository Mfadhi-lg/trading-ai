-- Additive research-only revision. Original SS01/02/03 facts remain immutable.
CREATE TABLE IF NOT EXISTS ss04_taxonomy(code TEXT PRIMARY KEY,label TEXT NOT NULL,definition_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ss04_decisions(company_id INTEGER PRIMARY KEY REFERENCES companies(company_id),major_scope INTEGER NOT NULL,primary_code TEXT REFERENCES ss04_taxonomy(code),status TEXT NOT NULL CHECK(status IN ('VERIFIED_TUTOR','VERIFIED_RESEARCH','PROVISIONAL','MULTI_ACTIVITY_REVIEW','UNRESOLVED')),decision_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ss04_exposures(company_id INTEGER REFERENCES companies(company_id),code TEXT REFERENCES ss04_taxonomy(code),role TEXT CHECK(role IN ('primary','secondary','unranked_activity')),evidence_json TEXT NOT NULL,PRIMARY KEY(company_id,code));
CREATE TABLE IF NOT EXISTS ss04_q2(entry_id TEXT PRIMARY KEY REFERENCES ss_focus_entries(entry_id),company_id INTEGER REFERENCES companies(company_id),decision_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ss04_run(signature TEXT PRIMARY KEY);
