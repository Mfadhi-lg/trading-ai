-- A host circuit is scheduling state, not an HTTP attempt result.  Older
-- workers bulk-labelled every untouched row on a blocked host as retryable,
-- which inflated failure counts by tens of thousands.  Restore only the
-- unmistakable zero-attempt rows; genuine attempted failures are preserved.
UPDATE artifact_download_queue
   SET status = 'PENDING',
       next_attempt_at_utc = NULL,
       last_result_status = NULL,
       last_error = NULL,
       updated_at_utc = CURRENT_TIMESTAMP
 WHERE status = 'RETRYABLE_FAILURE'
   AND attempt_count = 0
   AND last_result_status = 'HOST_CIRCUIT_OPEN';
