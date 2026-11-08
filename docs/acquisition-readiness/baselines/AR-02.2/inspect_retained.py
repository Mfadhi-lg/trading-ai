"""Read retained pre-migration copy only; never open a runtime/production DB."""
import json
import sqlite3
from pathlib import Path

root = Path(__file__).resolve().parents[4]
db = root / 'artifacts/acquisition-readiness/AR-01.3/pre-migration-068/raw/scraper.db'
assert db.is_file()
assert all(not Path(str(db) + suffix).exists() for suffix in ('-wal', '-shm'))
conn = sqlite3.connect(db.as_uri() + '?mode=ro&immutable=1', uri=True)
conn.row_factory = sqlite3.Row
conn.execute('PRAGMA query_only=ON')
queries = {
    'access_events': ("SELECT * FROM exchange_source_access_events WHERE dataset LIKE 'bse%' ORDER BY event_id", ()),
    'schedule_runs': ("SELECT * FROM exchange_schedule_runs WHERE dataset LIKE 'bse%' ORDER BY schedule_run_id DESC LIMIT 12", ()),
    'fetches': ("SELECT * FROM fetch_observations WHERE requested_url LIKE '%bseindia.com%' AND resource_kind LIKE '%exchange%' ORDER BY fetch_id DESC LIMIT 6", ()),
}
result = {'classification': 'RETAINED_COPY_READ_ONLY', 'db': str(db),
          'network_requests': 0, 'production_writes': 0,
          'whole_file_hash_recomputed': False}
for key, (query, args) in queries.items():
    result[key] = [dict(row) for row in conn.execute(query, args)]
conn.close()
print(json.dumps(result, indent=2))
