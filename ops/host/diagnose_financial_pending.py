"""Read-only reasons current-quarter financial rows are not claimable."""
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0,'/activation')
from sqlite_sidecar_guard import assert_safe_empty_wal, snapshot

db=Path('/data/scraper.db')
before=assert_safe_empty_wal(db)
conn=sqlite3.connect('file:/data/scraper.db?mode=ro&immutable=1',uri=True)
conn.row_factory=sqlite3.Row
conn.execute('PRAGMA query_only=ON')
conn.execute('PRAGMA temp_store=MEMORY')
now=datetime.now(timezone.utc).isoformat()
rows=[dict(row) for row in conn.execute('''SELECT status,
  coalesce(collection_disposition,'none') disposition,
  CASE WHEN collection_disposition IS NOT NULL THEN 'collection_disposition'
    WHEN EXISTS (SELECT 1 FROM screener_scope_aliases a
      JOIN screener_collection_scope s ON s.company_path=a.scope_path
      WHERE a.company_path=q.company_path_normalized AND s.eligibility='below_floor') THEN 'below_floor_alias'
    WHEN next_attempt_at_utc>? THEN 'future_due_time'
    ELSE 'due' END reason,
  count(*) n,min(next_attempt_at_utc) earliest_due,max(next_attempt_at_utc) latest_due
  FROM screener_company_queue q
  WHERE result_period='2026-09' AND status IN ('pending','failed_retryable')
  GROUP BY 1,2,3''',(now,))]
samples=[dict(row) for row in conn.execute('''SELECT company_path_normalized,
  next_attempt_at_utc,collection_disposition,last_error_json
  FROM screener_company_queue WHERE result_period='2026-09'
  AND status IN ('pending','failed_retryable') ORDER BY queue_item_id LIMIT 6''')]
conn.close()
if snapshot(db)!=before['files']:
    raise RuntimeError('Database changed during read-only inspection')
print(json.dumps({'current_quarter_pending':rows,'samples':samples,'observed_at_utc':now}))
