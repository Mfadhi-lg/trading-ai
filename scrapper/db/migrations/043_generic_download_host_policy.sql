-- DA-05.1: migrate generic queue work made ineligible by reviewed terms.

UPDATE artifact_download_queue AS queue
   SET status='SKIPPED_POLICY', next_attempt_at_utc=NULL,
       lease_owner=NULL, lease_expires_at_utc=NULL,
       last_result_status='PUBLISHER_TERMS_BLOCK_AUTOMATION',
       last_error='Automated acquisition disabled by reviewed publisher terms',
       updated_at_utc='2026-08-14T00:00:00+00:00'
 WHERE queue.status IN ('PENDING', 'LEASED', 'RETRYABLE_FAILURE')
   AND EXISTS (
     SELECT 1 FROM credit_rating_host_policy AS policy
      WHERE policy.automation_status='BLOCKED_TERMS'
        AND (
          queue.target_host=policy.host
          OR queue.target_host LIKE '%.' || policy.host
        )
   );

CREATE INDEX IF NOT EXISTS idx_artifact_download_queue_policy_host
ON artifact_download_queue(target_host, status, scope_id);
