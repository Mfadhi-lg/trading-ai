-- The original D09 worker checked only persisted rows, so transcript and PPT
-- expectations could exhaust their retry schedule without ever revisiting the
-- Screener company page. Give those affected result expectations one fresh,
-- source-backed retry ladder when this capability is deployed.

UPDATE artifact_expectations
   SET expected_after_utc = strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'),
       next_check_at_utc = strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now'),
       attempt_count = 0,
       terminal_reason = NULL,
       last_error_json = '{}',
       updated_at_utc = strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')
 WHERE artifact_type IN ('presentation', 'transcript')
   AND availability_status = 'expected_not_yet_published'
   AND terminal_reason = 'retry_schedule_exhausted'
   AND expectation_basis =
       'accepted_exchange_result_filing_requires_primary_source_discovery';
