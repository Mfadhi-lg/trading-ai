-- Earlier D09 checks were source-free and left active retry timestamps anchored
-- to old filing dates. Rebase every previously checked unresolved PPT and
-- transcript once, so the new source-backed passes follow the intended delays
-- instead of draining the entire ladder at five-minute worker intervals.

UPDATE artifact_expectations
   SET expected_after_utc = strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'),
       next_check_at_utc = strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'),
       attempt_count = 0,
       terminal_reason = NULL,
       last_error_json = '{}',
       updated_at_utc = strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')
 WHERE artifact_type IN ('presentation', 'transcript')
   AND availability_status IN (
       'expected_not_yet_published', 'discovery_failed', 'unknown', 'inaccessible'
   )
   AND expectation_basis =
       'accepted_exchange_result_filing_requires_primary_source_discovery'
   AND first_checked_at_utc IS NOT NULL
   AND event_id IN (
       SELECT event_id
         FROM artifact_events
        WHERE period_end <> '1970-01-01'
   );
