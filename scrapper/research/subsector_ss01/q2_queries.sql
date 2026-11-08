-- Deduplicated operational list (98 entries for the original Q2 snapshot).
SELECT * FROM ss_q2_focus ORDER BY display_stock_raw;

-- All original values and citations, including duplicate and incomplete source rows.
SELECT * FROM ss_q2_assessment_evidence WHERE entry_id=:entry_id ORDER BY original_row_number;

-- Reused historical subsectors and their original evidence; no overwrite of Q4/Q1.
SELECT e.display_stock_raw,p.state,d.label_raw,s.quarter,s.source_path,s.sha256,r.row_ref,
       a.known_at
FROM ss_focus_entries e JOIN ss_q2_subsector_status p USING(entry_id)
JOIN ss_inherited_subsector_evidence ie USING(entry_id)
JOIN ss_assignments a ON a.assignment_id=ie.prior_assignment_id
JOIN ss_subsectors d USING(subsector_id) JOIN ss_sources s ON s.source_id=a.source_id
JOIN ss_assignment_evidence ae ON ae.assignment_id=a.assignment_id
JOIN ss_rows r ON r.row_id=ae.row_id
WHERE e.entry_id=:entry_id;

-- Future ML linkage must gate on independently known historical availability.
-- With the delivered foundation this returns zero rows: no extraction was performed,
-- and no mentor decision/assessment availability timestamps have been established.
SELECT a.row_id,a.q2_results_raw,a.rating_raw,a.guidance_raw,f.fact_id,f.value_raw,
       f.agency_raw,doc.source_uri,cit.source_locator,cit.quote_raw
FROM ss_mentor_assessments a JOIN ss_assessment_fact_links link USING(row_id)
JOIN ss_extracted_facts f USING(fact_id) JOIN ss_documents doc USING(document_id)
JOIN ss_fact_citations cit USING(fact_id)
WHERE a.company_id=doc.company_id AND a.decision_at<=:as_of AND a.available_at<=:as_of
 AND doc.available_at<=:as_of AND f.available_at<=:as_of AND cit.available_at<=:as_of
 AND f.verification_state='verified';
