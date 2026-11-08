-- Historical exchange rows with no trustworthy reporting period used the
-- explicit epoch sentinel. These expectations cannot be matched safely to a
-- Screener concall and must not consume source-refresh attempts.

UPDATE artifact_expectations
   SET availability_status = 'source_not_covered',
       next_check_at_utc = NULL,
       terminal_reason = 'invalid_result_period_sentinel',
       last_error_json = '{}',
       claim_token = NULL,
       claimed_at_utc = NULL,
       claim_expires_at_utc = NULL,
       updated_at_utc = strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')
 WHERE event_id IN (
       SELECT event_id
         FROM artifact_events
        WHERE event_type = 'results' AND period_end = '1970-01-01'
   )
   AND availability_status = 'expected_not_yet_published';
