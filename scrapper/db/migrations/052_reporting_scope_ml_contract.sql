-- Standalone is the only financial reporting contract.  Raw exchange filings
-- may retain their source-declared scope elsewhere, but accepted financial
-- pages and ML eligibility cannot contain consolidated observations.
CREATE TABLE IF NOT EXISTS company_period_scope_policy (
    company_id INTEGER NOT NULL REFERENCES companies(company_id),
    period TEXT NOT NULL,
    reporting_scope TEXT NOT NULL CHECK (reporting_scope = 'standalone'),
    parse_id INTEGER NOT NULL REFERENCES parse_artifacts(parse_id),
    accepted_at_utc TEXT NOT NULL,
    PRIMARY KEY(company_id, period, reporting_scope)
);

CREATE INDEX IF NOT EXISTS idx_company_period_scope_policy_preference
ON company_period_scope_policy(company_id, period, reporting_scope);

-- Existing consolidated queue work is quarantined rather than rewritten as
-- standalone.  A future exchange filing can create a real standalone job.
UPDATE screener_company_queue
   SET status='failed_terminal',
       last_error_json='{"reason":"standalone_only_policy_rejection"}',
       claim_token=NULL, claimed_at_utc=NULL, claim_expires_at_utc=NULL
 WHERE reporting_scope='consolidated'
   AND status NOT IN ('accepted', 'skipped_already_accepted', 'failed_terminal');

-- Do not relabel an old consolidated parse.  It remains historical source
-- evidence only and is excluded from this ML-eligible policy table.
INSERT OR IGNORE INTO company_period_scope_policy
    (company_id, period, reporting_scope, parse_id, accepted_at_utc)
SELECT company_id, resolved_source_period, reporting_scope, parse_id, accepted_at_utc
FROM accepted_page_versions
WHERE source='screener'
  AND reporting_scope='standalone'
  AND resolved_source_period NOT IN ('', 'not_applicable');

CREATE VIEW IF NOT EXISTS ml_screener_standalone_pages AS
SELECT * FROM company_period_scope_policy
WHERE reporting_scope='standalone';

-- A durable audit surface for production checks and CI fixtures.  Any row
-- returned here is evidence that old consolidated data exists, never proof
-- that it can be used as standalone data.
CREATE VIEW IF NOT EXISTS financial_scope_integrity_audit AS
SELECT financial.financial_observation_id, financial.parse_id,
       financial.reporting_scope AS observation_scope,
       artifact.reporting_scope AS parse_scope
FROM financial_observations AS financial
JOIN parse_artifacts AS artifact ON artifact.parse_id=financial.parse_id
WHERE financial.reporting_scope <> 'standalone'
   OR artifact.reporting_scope <> 'standalone'
   OR financial.reporting_scope <> artifact.reporting_scope;
