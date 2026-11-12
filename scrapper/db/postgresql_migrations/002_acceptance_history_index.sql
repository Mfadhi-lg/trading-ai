-- Issue #56: company_quarter_features reads the complete company/scope
-- acceptance history, including rejected and superseded versions.
-- Keep every row and the existing timestamp/text semantics unchanged.
-- Evidence: docs/database/POSTGRESQL_PERFORMANCE.md and performance/issue56.json.
CREATE INDEX idx_acceptance_events_company_scope
ON acceptance_events(company_id, reporting_scope);
