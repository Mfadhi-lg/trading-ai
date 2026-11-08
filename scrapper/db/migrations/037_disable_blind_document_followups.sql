-- Documents must be collected only after an official NSE/BSE announcement.
-- Retire the temporary timer-driven states so accepted result jobs are never
-- re-opened merely because a presentation or transcript is absent.
UPDATE company_document_followups
SET status = 'not_offered',
    next_check_at_utc = NULL,
    updated_at_utc = strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')
WHERE status IN ('pending', 'checking');
