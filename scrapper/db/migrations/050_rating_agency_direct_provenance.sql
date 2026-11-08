-- Recognize exact, production-observed CRA publisher hosts as official direct
-- retrieval surfaces. Blocked-host automation policy is preserved unchanged.

UPDATE artifact_sources
   SET retrieval_source_type=COALESCE(retrieval_source_type,'RATING_AGENCY_DIRECT'),
       retrieval_host=CASE
         WHEN lower(document_url) LIKE 'http%://www.careratings.com/%' THEN 'www.careratings.com'
         WHEN lower(document_url) LIKE 'http%://www.indiaratings.co.in/%' THEN 'www.indiaratings.co.in'
         WHEN lower(document_url) LIKE 'http%://www.crisil.com/%' THEN 'www.crisil.com'
         WHEN lower(document_url) LIKE 'http%://%.crisil.com/%' THEN 'crisil.com'
         WHEN lower(document_url) LIKE 'http%://www.crisilratings.com/%' THEN 'www.crisilratings.com'
         WHEN lower(document_url) LIKE 'http%://%.crisilratings.com/%' THEN 'crisilratings.com'
         WHEN lower(document_url) LIKE 'http%://www.icra.in/%' THEN 'www.icra.in'
         WHEN lower(document_url) LIKE 'http%://%.icra.in/%' THEN 'icra.in'
         WHEN lower(document_url) LIKE 'http%://www.infomerics.com/%' THEN 'www.infomerics.com'
         WHEN lower(document_url) LIKE 'http%://www.acuite.in/%' THEN 'www.acuite.in'
         WHEN lower(document_url) LIKE 'http%://connect.acuite.in/%' THEN 'connect.acuite.in'
         WHEN lower(document_url) LIKE 'http%://bcrisp.in/%' THEN 'bcrisp.in'
         WHEN lower(document_url) LIKE 'http%://www.bcrisp.in/%' THEN 'www.bcrisp.in'
         WHEN lower(document_url) LIKE 'http%://www.brickworkratings.com/%' THEN 'www.brickworkratings.com'
         ELSE retrieval_host
       END,
       is_primary_source=1,
       automation_status=CASE
         WHEN automation_status='BLOCKED_TERMS' THEN automation_status
         ELSE 'AVAILABLE'
       END
 WHERE lower(document_url) LIKE 'http%://www.careratings.com/%'
    OR lower(document_url) LIKE 'http%://www.indiaratings.co.in/%'
    OR lower(document_url) LIKE 'http%://www.crisil.com/%'
    OR lower(document_url) LIKE 'http%://%.crisil.com/%'
    OR lower(document_url) LIKE 'http%://www.crisilratings.com/%'
    OR lower(document_url) LIKE 'http%://%.crisilratings.com/%'
    OR lower(document_url) LIKE 'http%://www.icra.in/%'
    OR lower(document_url) LIKE 'http%://%.icra.in/%'
    OR lower(document_url) LIKE 'http%://www.infomerics.com/%'
    OR lower(document_url) LIKE 'http%://www.acuite.in/%'
    OR lower(document_url) LIKE 'http%://connect.acuite.in/%'
    OR lower(document_url) LIKE 'http%://bcrisp.in/%'
    OR lower(document_url) LIKE 'http%://www.bcrisp.in/%'
    OR lower(document_url) LIKE 'http%://www.brickworkratings.com/%';

CREATE TEMP TABLE _official_direct_rating_content_ids (
  content_id INTEGER PRIMARY KEY
);

INSERT OR IGNORE INTO _official_direct_rating_content_ids(content_id)
SELECT attempt.content_id
  FROM artifact_download_attempts AS attempt
  JOIN artifact_download_attempt_sources AS link USING(download_attempt_id)
  JOIN artifact_sources AS source USING(artifact_source_id)
 WHERE attempt.content_id IS NOT NULL
   AND attempt.result_status='validated'
   AND source.retrieval_source_type='RATING_AGENCY_DIRECT';

UPDATE artifact_contents
   SET is_official=1
 WHERE content_id IN (SELECT content_id FROM _official_direct_rating_content_ids);

DROP TABLE _official_direct_rating_content_ids;
