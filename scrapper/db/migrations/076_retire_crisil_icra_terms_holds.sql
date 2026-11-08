-- Retire the repository-imposed CRISIL/ICRA terms holds at user request.
-- Keep released migration identities, completed work, retries and host cooldowns.

UPDATE credit_rating_host_policy
   SET automation_status='UNSPECIFIED', max_concurrency=1,
       min_delay_seconds=MAX(min_delay_seconds, 4),
       policy_basis='Repository terms hold removed by user request on 2026-10-30; standard paced acquisition.',
       reviewed_at_utc='2026-10-30T00:00:00+00:00'
 WHERE host IN ('crisil.com', 'crisilratings.com', 'icra.in')
    OR host LIKE '%.crisil.com'
    OR host LIKE '%.crisilratings.com'
    OR host LIKE '%.icra.in';

UPDATE artifact_sources
   SET automation_status='AUTOMATION_ELIGIBILITY_UNKNOWN'
 WHERE automation_status='BLOCKED_TERMS'
   AND (lower(document_url) LIKE '%://crisil.com/%'
     OR lower(document_url) LIKE '%://%.crisil.com/%'
     OR lower(document_url) LIKE '%://crisilratings.com/%'
     OR lower(document_url) LIKE '%://%.crisilratings.com/%'
     OR lower(document_url) LIKE '%://icra.in/%'
     OR lower(document_url) LIKE '%://%.icra.in/%');

UPDATE credit_rating_download_queue
   SET status='PENDING', next_attempt_at_utc=NULL,
       lease_owner=NULL, lease_expires_at_utc=NULL,
       last_result_status=NULL, last_error=NULL,
       updated_at_utc='2026-10-30T00:00:00+00:00'
 WHERE status='SKIPPED_POLICY'
   AND last_result_status IN ('PUBLISHER_TERMS_BLOCK_AUTOMATION', 'PUBLISHER_POLICY_BLOCKED')
   AND (target_host IN ('crisil.com', 'crisilratings.com', 'icra.in')
     OR target_host LIKE '%.crisil.com'
     OR target_host LIKE '%.crisilratings.com'
     OR target_host LIKE '%.icra.in');

UPDATE artifact_download_queue
   SET status='PENDING', next_attempt_at_utc=NULL,
       lease_owner=NULL, lease_expires_at_utc=NULL,
       last_result_status=NULL, last_error=NULL,
       updated_at_utc='2026-10-30T00:00:00+00:00'
 WHERE status='SKIPPED_POLICY'
   AND last_result_status IN ('PUBLISHER_TERMS_BLOCK_AUTOMATION', 'PUBLISHER_POLICY_BLOCKED')
   AND (target_host IN ('crisil.com', 'crisilratings.com', 'icra.in')
     OR target_host LIKE '%.crisil.com'
     OR target_host LIKE '%.crisilratings.com'
     OR target_host LIKE '%.icra.in');
