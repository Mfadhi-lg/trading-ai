-- SS-03 additive overlay. Original SS-01/02 records are immutable.
CREATE TABLE IF NOT EXISTS ss03_canonical (canonical_id TEXT PRIMARY KEY, label TEXT NOT NULL UNIQUE, review_status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS ss03_alias (subsector_id TEXT PRIMARY KEY REFERENCES ss_subsectors(subsector_id), canonical_id TEXT NOT NULL REFERENCES ss03_canonical(canonical_id), reason TEXT NOT NULL, review_status TEXT NOT NULL);
CREATE VIEW IF NOT EXISTS ss03_historical_memberships AS
 SELECT DISTINCT s.quarter, COALESCE(i.company_id,r.company_id) AS company_id, a.canonical_id
 FROM ss_rows r JOIN ss_sources s USING(source_id)
 JOIN ss_subsectors t ON t.label_raw=r.label_raw JOIN ss03_alias a USING(subsector_id)
 LEFT JOIN ss02_identity_overlay i USING(row_id)
 WHERE s.quarter IN ('FY2026-Q4','FY2027-Q1') AND COALESCE(i.company_id,r.company_id) IS NOT NULL;
