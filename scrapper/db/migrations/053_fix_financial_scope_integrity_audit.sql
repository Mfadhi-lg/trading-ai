-- parse_artifacts describes parser provenance, not reporting scope.  The
-- quote observation is the parse-level source of truth for that scope.
DROP VIEW IF EXISTS financial_scope_integrity_audit;
CREATE VIEW financial_scope_integrity_audit AS
SELECT financial.financial_observation_id, financial.parse_id,
       financial.reporting_scope AS observation_scope,
       quote.reporting_scope AS parse_scope
FROM financial_observations AS financial
LEFT JOIN quote_observations AS quote ON quote.parse_id=financial.parse_id
WHERE financial.reporting_scope <> 'standalone'
   OR quote.reporting_scope IS NULL
   OR quote.reporting_scope <> 'standalone'
   OR financial.reporting_scope <> quote.reporting_scope;
