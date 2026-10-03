-- Preserve Screener as the discovery provenance while recognizing official
-- NSE/BSE retrieval hosts as official source delivery.  For rating documents
-- document_originator remains CRISIL/ICRA; this migration never changes it.

UPDATE artifact_sources
   SET retrieval_source_type=CASE
         WHEN lower(document_url) LIKE '%://bseindia.com/%'
           OR lower(document_url) LIKE '%://%.bseindia.com/%'
           THEN COALESCE(retrieval_source_type,'BSE_EXCHANGE_ATTACHMENT')
         WHEN lower(document_url) LIKE '%://nseindia.com/%'
           OR lower(document_url) LIKE '%://%.nseindia.com/%'
           THEN COALESCE(retrieval_source_type,'NSE_EXCHANGE_ATTACHMENT')
         ELSE retrieval_source_type
       END,
       retrieval_host=CASE
         WHEN lower(document_url) LIKE '%://bseindia.com/%'
           OR lower(document_url) LIKE '%://%.bseindia.com/%'
           THEN COALESCE(retrieval_host,'www.bseindia.com')
         WHEN lower(document_url) LIKE '%://nseindia.com/%'
           OR lower(document_url) LIKE '%://%.nseindia.com/%'
           THEN COALESCE(retrieval_host,
             CASE
               WHEN lower(document_url) LIKE '%://nsearchives.nseindia.com/%'
                 THEN 'nsearchives.nseindia.com'
               WHEN lower(document_url) LIKE '%://archives.nseindia.com/%'
                 THEN 'archives.nseindia.com'
               ELSE 'www.nseindia.com'
             END)
         ELSE retrieval_host
       END,
       is_primary_source=1
 WHERE lower(document_url) LIKE '%://bseindia.com/%'
    OR lower(document_url) LIKE '%://%.bseindia.com/%'
    OR lower(document_url) LIKE '%://nseindia.com/%'
    OR lower(document_url) LIKE '%://%.nseindia.com/%';

CREATE TEMP TABLE _official_exchange_content_ids (
  content_id INTEGER PRIMARY KEY
);

INSERT OR IGNORE INTO _official_exchange_content_ids(content_id)
SELECT attempt.content_id
  FROM artifact_download_attempts AS attempt
  JOIN artifact_download_attempt_sources AS link USING(download_attempt_id)
  JOIN artifact_sources AS source USING(artifact_source_id)
 WHERE attempt.content_id IS NOT NULL
   AND attempt.result_status='validated'
   AND source.retrieval_source_type IN (
     'BSE_EXCHANGE_ATTACHMENT','NSE_EXCHANGE_ATTACHMENT',
     'BSE_DEBT_DOCUMENT','NSE_DEBT_DOCUMENT'
   );

UPDATE artifact_contents
   SET is_official=1
 WHERE content_id IN (SELECT content_id FROM _official_exchange_content_ids);

DROP TABLE _official_exchange_content_ids;
