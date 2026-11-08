-- Repeated Screener passes for delayed result presentations and transcripts
-- are research-scope work. Keep them only for companies whose latest accepted
-- standalone Screener page reports market cap strictly above INR 580 crore.

UPDATE artifact_expectations
   SET availability_status = 'source_not_covered',
       next_check_at_utc = NULL,
       terminal_reason = 'market_cap_not_above_580_crore',
       last_error_json = '{"type":"ScreenerMarketCapOutOfScope","message":"Latest accepted Screener market cap is not above 580 INR crore"}',
       claim_token = NULL,
       claimed_at_utc = NULL,
       claim_expires_at_utc = NULL,
       updated_at_utc = strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')
 WHERE artifact_type IN ('presentation', 'transcript')
   AND expectation_basis =
       'accepted_exchange_result_filing_requires_primary_source_discovery'
   AND availability_status IN (
       'expected_not_yet_published', 'discovery_failed', 'unknown', 'inaccessible'
   )
   AND NOT EXISTS (
       SELECT 1
         FROM accepted_page_versions AS accepted
         JOIN company_metric_observations AS metric
           ON metric.parse_id = accepted.parse_id
        WHERE accepted.company_id = artifact_expectations.company_id
          AND accepted.source = 'screener'
          AND accepted.reporting_scope = 'standalone'
          AND metric.metric_code = 'market_cap'
          AND CAST(metric.value_decimal AS REAL) > 580.0
          AND accepted.parse_id = (
              SELECT latest.parse_id
                FROM accepted_page_versions AS latest
               WHERE latest.company_id = accepted.company_id
                 AND latest.source = 'screener'
                 AND latest.reporting_scope = 'standalone'
               ORDER BY latest.accepted_at_utc DESC, latest.parse_id DESC
               LIMIT 1
          )
   );
