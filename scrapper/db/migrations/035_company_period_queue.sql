-- One company publishing one quarter's result is one collection job.  Older
-- queue rows were keyed by a Screener page variant (plain versus
-- /consolidated/), so preserve their raw evidence but retire all but one queue
-- outcome for each company-period.
WITH ranked AS (
  SELECT queue_item_id,
         ROW_NUMBER() OVER (
           PARTITION BY replace(company_path_normalized, '/consolidated/', '/'),
                        result_period
           ORDER BY
             CASE status WHEN 'accepted' THEN 0 ELSE 1 END,
             queue_item_id
         ) AS position
  FROM screener_company_queue
)
UPDATE screener_company_queue
   SET status='skipped_already_accepted',
       claim_token=NULL,
       claimed_at_utc=NULL,
       claim_expires_at_utc=NULL,
       last_error_json='{"reason":"duplicate_company_period"}'
 WHERE queue_item_id IN (
   SELECT queue_item_id FROM ranked WHERE position > 1
 );
