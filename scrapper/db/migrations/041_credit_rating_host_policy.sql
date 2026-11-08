-- Auditable access policy and conservative per-host worker limits.

CREATE TABLE IF NOT EXISTS credit_rating_host_policy (
    host TEXT PRIMARY KEY,
    automation_status TEXT NOT NULL CHECK (
      automation_status IN ('BLOCKED_TERMS', 'NOT_DISALLOWED_ROBOTS', 'UNSPECIFIED')
    ),
    max_concurrency INTEGER NOT NULL DEFAULT 1 CHECK (max_concurrency BETWEEN 0 AND 8),
    min_delay_seconds REAL NOT NULL DEFAULT 4 CHECK (min_delay_seconds >= 0),
    policy_basis TEXT NOT NULL,
    source_url TEXT,
    reviewed_at_utc TEXT NOT NULL
);

INSERT INTO credit_rating_host_policy
  (host, automation_status, max_concurrency, min_delay_seconds,
   policy_basis, source_url, reviewed_at_utc)
VALUES
  ('www.crisil.com', 'BLOCKED_TERMS', 0, 0,
   'Website terms prohibit robots, bots, spiders, scrapers, and automated processes.',
   'https://www.crisil.com/en/home/website-terms-of-use.html',
   '2026-08-14T00:00:00+00:00'),
  ('crisil.com', 'BLOCKED_TERMS', 0, 0,
   'Website terms prohibit robots, bots, spiders, scrapers, and automated processes.',
   'https://www.crisil.com/en/home/website-terms-of-use.html',
   '2026-08-14T00:00:00+00:00'),
  ('www.icra.in', 'BLOCKED_TERMS', 0, 0,
   'Terms prohibit scraping, data mining, and systematic extraction into a database.',
   'https://www.icra.in/Home/Disclaimer',
   '2026-08-14T00:00:00+00:00'),
  ('icra.in', 'BLOCKED_TERMS', 0, 0,
   'Terms prohibit scraping, data mining, and systematic extraction into a database.',
   'https://www.icra.in/Home/Disclaimer',
   '2026-08-14T00:00:00+00:00'),
  ('www.careratings.com', 'NOT_DISALLOWED_ROBOTS', 1, 4,
   'Public robots policy does not disallow crawling; this is not treated as legal permission.',
   'https://www.careratings.com/robots.txt',
   '2026-08-14T00:00:00+00:00'),
  ('www.indiaratings.co.in', 'NOT_DISALLOWED_ROBOTS', 1, 4,
   'Public robots policy does not disallow the press-release path; this is not treated as legal permission.',
   'https://www.indiaratings.co.in/robots.txt',
   '2026-08-14T00:00:00+00:00'),
  ('www.infomerics.com', 'UNSPECIFIED', 1, 4,
   'No usable published robots policy or explicit automation permission found; conservative single stream.',
   NULL, '2026-08-14T00:00:00+00:00'),
  ('www.acuite.in', 'UNSPECIFIED', 1, 4,
   'No usable published robots policy or explicit automation permission found; conservative single stream.',
   NULL, '2026-08-14T00:00:00+00:00'),
  ('connect.acuite.in', 'UNSPECIFIED', 1, 4,
   'No usable published robots policy or explicit automation permission found; conservative single stream.',
   NULL, '2026-08-14T00:00:00+00:00'),
  ('bcrisp.in', 'UNSPECIFIED', 1, 4,
   'No usable published robots policy or explicit automation permission found; conservative single stream.',
   NULL, '2026-08-14T00:00:00+00:00'),
  ('www.bcrisp.in', 'UNSPECIFIED', 1, 4,
   'No usable published robots policy or explicit automation permission found; conservative single stream.',
   NULL, '2026-08-14T00:00:00+00:00'),
  ('www.brickworkratings.com', 'UNSPECIFIED', 1, 4,
   'No usable published robots policy or explicit automation permission found; conservative single stream.',
   NULL, '2026-08-14T00:00:00+00:00')
ON CONFLICT(host) DO UPDATE SET
  automation_status=excluded.automation_status,
  max_concurrency=excluded.max_concurrency,
  min_delay_seconds=excluded.min_delay_seconds,
  policy_basis=excluded.policy_basis,
  source_url=excluded.source_url,
  reviewed_at_utc=excluded.reviewed_at_utc;

UPDATE credit_rating_download_queue
   SET status='SKIPPED_POLICY', next_attempt_at_utc=NULL,
       lease_owner=NULL, lease_expires_at_utc=NULL,
       last_result_status='PUBLISHER_TERMS_BLOCK_AUTOMATION',
       last_error='Automated acquisition disabled by reviewed publisher terms',
       updated_at_utc='2026-08-14T00:00:00+00:00'
 WHERE target_host IN (
   SELECT host FROM credit_rating_host_policy WHERE automation_status='BLOCKED_TERMS'
 )
   AND status IN ('PENDING', 'LEASED', 'RETRYABLE_FAILURE', 'INTERACTION_REQUIRED');

CREATE INDEX IF NOT EXISTS idx_credit_rating_host_policy_status
ON credit_rating_host_policy(automation_status, host);
