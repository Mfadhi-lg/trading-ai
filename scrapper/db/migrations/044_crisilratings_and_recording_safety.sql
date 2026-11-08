-- DA-05.2: close CRISIL Ratings automation and automatic recording downloads.

INSERT INTO credit_rating_host_policy
  (host, automation_status, max_concurrency, min_delay_seconds,
   policy_basis, source_url, reviewed_at_utc)
VALUES
  ('www.crisilratings.com', 'BLOCKED_TERMS', 0, 0,
   'Website terms prohibit robots, bots, spiders, scrapers, and automated processes.',
   'https://www.crisilratings.com/en/home/website-terms-of-use.html',
   '2026-08-14T00:00:00+00:00'),
  ('crisilratings.com', 'BLOCKED_TERMS', 0, 0,
   'Website terms prohibit robots, bots, spiders, scrapers, and automated processes.',
   'https://www.crisilratings.com/en/home/website-terms-of-use.html',
   '2026-08-14T00:00:00+00:00')
ON CONFLICT(host) DO UPDATE SET
  automation_status=excluded.automation_status,
  max_concurrency=excluded.max_concurrency,
  min_delay_seconds=excluded.min_delay_seconds,
  policy_basis=excluded.policy_basis,
  source_url=excluded.source_url,
  reviewed_at_utc=excluded.reviewed_at_utc;

UPDATE credit_rating_download_queue AS queue
   SET status='SKIPPED_POLICY', next_attempt_at_utc=NULL,
       lease_owner=NULL, lease_expires_at_utc=NULL,
       last_result_status='PUBLISHER_TERMS_BLOCK_AUTOMATION',
       last_error='Automated acquisition disabled by reviewed publisher terms',
       updated_at_utc='2026-08-14T00:00:00+00:00'
 WHERE queue.status IN (
   'PENDING', 'LEASED', 'RETRYABLE_FAILURE', 'INTERACTION_REQUIRED'
 )
   AND (
     queue.target_host='crisilratings.com'
     OR queue.target_host LIKE '%.crisilratings.com'
   );

UPDATE artifact_download_queue AS queue
   SET status='SKIPPED_POLICY', next_attempt_at_utc=NULL,
       lease_owner=NULL, lease_expires_at_utc=NULL,
       last_result_status='PUBLISHER_TERMS_BLOCK_AUTOMATION',
       last_error='Automated acquisition disabled by reviewed publisher terms',
       updated_at_utc='2026-08-14T00:00:00+00:00'
 WHERE queue.status IN ('PENDING', 'LEASED', 'RETRYABLE_FAILURE')
   AND (
     queue.target_host='crisilratings.com'
     OR queue.target_host LIKE '%.crisilratings.com'
   );

-- Recordings remain promoted artifacts, but the accepted-page automation must
-- never enqueue or claim their media bytes.
UPDATE artifact_download_queue AS queue
   SET status='SKIPPED_POLICY', next_attempt_at_utc=NULL,
       lease_owner=NULL, lease_expires_at_utc=NULL,
       last_result_status='AUTOMATIC_RECORDING_DOWNLOAD_DISABLED',
       last_error='Recording retained as promoted metadata; automatic download disabled',
       updated_at_utc='2026-08-14T00:00:00+00:00'
 WHERE queue.scope_id='accepted-screener-concall'
   AND queue.status IN ('PENDING', 'LEASED', 'RETRYABLE_FAILURE')
   AND EXISTS (
     SELECT 1 FROM document_artifacts AS artifact
      WHERE artifact.artifact_id=queue.artifact_id
        AND artifact.artifact_type='recording'
   );

DELETE FROM artifact_download_scope_artifacts
 WHERE scope_id='accepted-screener-concall'
   AND artifact_id IN (
     SELECT artifact_id FROM document_artifacts WHERE artifact_type='recording'
   );

UPDATE artifact_download_scopes
   SET artifact_types_json='["transcript","presentation"]',
       policy_json='{"pdf_only":false,"recording_fallback_only":false}',
       description='Automatic primary transcript and presentation acquisition from accepted Screener pages',
       updated_at_utc='2026-08-14T00:00:00+00:00'
 WHERE scope_id='accepted-screener-concall';
