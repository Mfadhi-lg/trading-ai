"""Read-only production baseline inspection; no migration or schema changes."""
import hashlib
import json
import sqlite3
from pathlib import Path

db = Path('/data/scraper.db')
assert db.is_file(), 'existing DB missing'
assert not Path(str(db)+'-wal').exists(), 'WAL present: immutable read forbidden'
conn = sqlite3.connect('file:/data/scraper.db?mode=ro&immutable=1', uri=True)
conn.execute('PRAGMA query_only=ON')
ledger = list(conn.execute('SELECT * FROM schema_migrations ORDER BY version'))
objects = list(conn.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name'))
result = {'ledger': ledger, 'schema': objects, 'sidecars': {
    suffix: {'exists': Path(str(db)+suffix).exists(), 'bytes': Path(str(db)+suffix).stat().st_size if Path(str(db)+suffix).exists() else None}
    for suffix in ('-wal','-shm')}, 'queue_states': {}, 'possible_active_claims': {}}
for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
    if not any(x in name for x in ('queue', 'jobs', 'work', 'runs')):
        continue
    q = '"'+name.replace('"','""')+'"'
    cols = [r[1] for r in conn.execute('PRAGMA table_info('+q+')')]
    state = next((x for x in ('status','state','job_status','run_status') if x in cols), None)
    if state:
        result['queue_states'][name] = list(conn.execute('SELECT "'+state+'", COUNT(*) FROM '+q+' GROUP BY "'+state+'"'))
        active = list(conn.execute('SELECT * FROM '+q+' WHERE lower("'+state+'") IN (\'running\',\'in_progress\',\'processing\',\'claimed\',\'leased\') LIMIT 101'))
        if active:
            result['possible_active_claims'][name] = {'columns': cols, 'rows': active}
result['markers'] = {}
for p in (Path('/data/.trading_ai_runtime_identity.json'),Path('/data/snapshots/.trading_ai_runtime_identity.json')):
    result['markers'][str(p)] = {'exists': p.is_file(), 'sha256': hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None}
conn.close()
print(json.dumps(result,indent=2))
