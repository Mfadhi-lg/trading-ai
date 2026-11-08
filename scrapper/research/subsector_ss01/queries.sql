-- Bind :label to an EXACT source label; source categories have no invented hierarchy.
SELECT DISTINCT company_id,current_legal_name,e.quarter,e.label_raw
FROM ss_classification_evidence e JOIN companies USING(company_id)
WHERE e.label_raw=:label AND e.subsector_id IS NOT NULL;

-- All source labels represented in a quarter, including unresolved source identities.
SELECT DISTINCT label_raw FROM ss_classification_evidence
WHERE quarter=:quarter ORDER BY label_raw;

SELECT company_id,current_legal_name FROM companies WHERE company_id IN (
 SELECT company_id FROM ss_memberships
 WHERE quarter IN ('FY2026-Q4','FY2027-Q1') GROUP BY company_id
 HAVING count(DISTINCT quarter)=2
);

-- Known as of a historical date. Unknown dates NEVER inherit quarter-end dates.
-- Retain all competing evidence; no arbitrary "latest row wins" classification.
-- For these inputs this correctly returns no rows: date evidence is absent.
SELECT * FROM ss_classification_evidence
WHERE company_id=:company_id AND known_at IS NOT NULL AND known_at<=:as_of
 AND (valid_from IS NULL OR valid_from<=:as_of)
 AND (valid_to IS NULL OR valid_to>:as_of);

-- Evidence attached to a historical quarter (different from known as of that date).
SELECT * FROM ss_classification_evidence WHERE company_id=:company_id
ORDER BY quarter,source_path,row_ref;

-- Explicit unknown-date evidence and identity review queue.
SELECT * FROM ss_classification_evidence WHERE known_at IS NULL;
SELECT * FROM ss_classification_evidence WHERE resolution_state!='resolved';
