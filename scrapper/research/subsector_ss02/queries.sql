-- Full canonical-universe coverage; broad descriptors never count as completed.
SELECT status,count(*) FROM ss02_company_mapping GROUP BY status;
SELECT * FROM ss02_company_subsectors WHERE company_id=:company_id;

-- Separately evidenced secondary/unranked business exposures.
SELECT e.company_id,c.current_legal_name,t.label,e.role,e.evidence_json
FROM ss02_business_exposures e JOIN companies c USING(company_id)
JOIN ss02_taxonomy t USING(code) WHERE e.company_id=:company_id;

-- The tutor's historical labels and original source paths/row references remain intact.
SELECT r.row_id,r.label_raw,s.quarter,s.source_path,s.sha256,r.row_ref,r.raw_text,
       i.company_id resolved_company_id,i.state
FROM ss_rows r JOIN ss_sources s USING(source_id) JOIN ss02_identity_overlay i USING(row_id)
WHERE i.company_id=:company_id ORDER BY s.quarter,r.row_ref;

-- Q2 joins assessments without editing judgments or selecting duplicate conflicts.
SELECT * FROM ss02_q2_assessment_context WHERE resolved_company_id=:company_id;

-- One preserved Q2 entry, including review states and independent classification.
SELECT q.*,e.display_stock_raw,e.assessment_state FROM ss02_q2_mapping q
JOIN ss_focus_entries e USING(entry_id) ORDER BY e.display_stock_raw;

-- No current research is retrospectively available at a March/June cutoff.
SELECT * FROM ss02_company_subsectors
WHERE julianday(known_at)<=julianday(:as_of) AND primary_code IS NOT NULL;

-- Complete existing security inventory, including absent/ambiguous canonical links.
SELECT state,count(*) FROM ss02_security_mapping GROUP BY state;

-- Frozen metadata-qualified constituents; candidate-only baskets stay distinguishable.
SELECT b.code,b.quarter,b.cutoff_at,b.frozen_at,b.status,b.chart_ready,
       m.company_id,c.current_legal_name,m.rank,m.equal_weight,m.historical_cap_weight,
       m.selection_evidence_json
FROM ss02_baskets b LEFT JOIN ss02_basket_members m USING(basket_id)
LEFT JOIN companies c USING(company_id) ORDER BY b.code,b.quarter,m.rank;
