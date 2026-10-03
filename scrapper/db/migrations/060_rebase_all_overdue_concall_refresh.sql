-- Include valid result-document expectations which were queued in the past but
-- never reached by the old source-free worker. Their first real Screener pass
-- also needs a retry ladder anchored at deployment time.

UPDATE artifact_expectations
   SET expected_after_utc = strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'),
       next_check_at_utc = strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'),
       attempt_count = 0,
       terminal_reason = NULL,
       last_error_json = '{}',
       claim_token = NULL,
       claimed_at_utc = NULL,
       claim_expires_at_utc = NULL,
       updated_at_utc = strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')
 WHERE artifact_type IN ('presentation', 'transcript')
   AND availability_status IN (
       'expected_not_yet_published', 'discovery_failed', 'unknown', 'inaccessible'
   )
   AND expectation_basis =
       'accepted_exchange_result_filing_requires_primary_source_discovery'
   AND next_check_at_utc IS NOT NULL
   AND next_check_at_utc <= strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')
   AND event_id IN (
       SELECT event_id
         FROM artifact_events
        WHERE period_end <> '1970-01-01'
   );
