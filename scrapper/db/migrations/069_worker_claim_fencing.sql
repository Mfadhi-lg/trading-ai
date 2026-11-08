-- C19.2: unique generation for each generic, direct-rating and browser claim.
-- Nullable for compatibility with existing history and older readers.
-- Apply explicitly on release; no attempt/history reset or queue redesign.
ALTER TABLE artifact_download_queue ADD COLUMN claim_token TEXT;
ALTER TABLE credit_rating_download_queue ADD COLUMN claim_token TEXT;
ALTER TABLE browser_rating_recovery_jobs ADD COLUMN claim_token TEXT;
