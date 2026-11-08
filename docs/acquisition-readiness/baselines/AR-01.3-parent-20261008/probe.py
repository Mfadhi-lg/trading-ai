"""Finite production inspector only: no connect/init/migration/ownership APIs."""
import hashlib
import json
import os
import signal
import sqlite3
import stat
import sys
import time
from pathlib import Path

def stop(*args):
    raise RuntimeError('20-second probe deadline; stop and preserve evidence')
signal.signal(signal.SIGALRM, stop)
signal.alarm(20)
sys.path.insert(0, '/app')
from scrapper.db.database import check_schema_compatibility

proof = Path('/proof')
baseline = json.loads((proof / 'baseline.json').read_text())
gate = json.loads((proof / sys.argv[1] / 'writer-before.json').read_text())
assert gate['writer_gate_passed'] and 0 <= time.time() - gate['observed_at_epoch'] <= 10, 'Stale owner gate'
assert hashlib.sha256(Path(__file__).read_bytes()).hexdigest() == gate['harness_sha256'], 'Inspector drift'
mounts = [l.split(' - ', 1)[0].split() for l in Path('/proc/self/mountinfo').read_text().splitlines()]
for target in ('/data', '/proof', '/'):
    assert any(r[4] == target and 'ro' in r[5].split(',') for r in mounts), 'Writable/missing mount: ' + target
db = Path('/data/scraper.db')
def state():
    s = db.lstat()
    assert stat.S_ISREG(s.st_mode), 'Nonregular DB'
    sidecars = {x: os.path.lexists(str(db) + x) for x in ('-wal', '-shm', '-journal')}
    assert not any(sidecars.values()), 'Unsafe WAL/SHM/journal: no immutable read'
    return {'bytes': s.st_size, 'mtime_ns': s.st_mtime_ns, 'ctime_ns': s.st_ctime_ns,
            'device': s.st_dev, 'inode': s.st_ino, 'sidecars': sidecars}
before = state()
assert before == baseline['db_after'], 'DB identity differs from completed C proof'
with sqlite3.connect('file:/data/scraper.db?mode=ro&immutable=1', uri=True) as conn:
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    assert conn.execute('PRAGMA query_only').fetchone()[0] == 1
    assert conn.execute('SELECT 1').fetchone()[0] == 1
    check_schema_compatibility(conn)
    schema = [dict(r) for r in conn.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
    ledger = [dict(r) for r in conn.execute('SELECT * FROM schema_migrations ORDER BY version')]
    assert schema == baseline['schema'] and ledger == baseline['ledger'], 'Schema/ledger drift'
    assert len(ledger) == 76 and ledger[-1]['version'] == '075_periodic_dispatch.sql'
    row = dict(conn.execute('SELECT * FROM document_extraction_claims WHERE resource_key=?',
                           (baseline['control_row']['resource_key'],)).fetchone())
    assert row == baseline['control_row'] and row['status'] == 'RELEASED', 'Control receipt drift'
    assert conn.execute('SELECT COUNT(*) FROM document_extraction_claims').fetchone()[0] == 1
    empty = ['acquisition_host_state', 'acquisition_host_permits', 'document_processing_inbox',
             'document_processing_work', 'exchange_coordinator_tasks', 'collection_scope_tasks', 'periodic_dispatch_tasks']
    assert all(conn.execute('SELECT COUNT(*) FROM ' + t).fetchone()[0] == 0 for t in empty), 'Unexpected admitted work'
    assert conn.total_changes == 0, 'Unexpected SQL change'
conn.close()
markers = {}
for name in baseline['physical_markers']:
    p = Path(name)
    markers[name] = {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(), 'bytes': p.stat().st_size}
assert markers == baseline['physical_markers'], 'Physical marker drift'
h = hashlib.sha256()
with db.open('rb') as f:
    for block in iter(lambda: f.read(8 * 1024 * 1024), b''):
        h.update(block)
assert h.hexdigest() == baseline['db_sha256'], 'DB bytes differ; cannot reuse full row/FK proof'
after = state()
assert after == before, 'DB identity changed during probe'
signal.alarm(0)
print(json.dumps({'status': 'PASS', 'select_1': 1, 'schema_compatibility': 'PASS',
                  'ledger_count': len(ledger), 'latest_schema': ledger[-1]['version'],
                  'db_before': before, 'db_after': after, 'db_sha256': h.hexdigest(),
                  'schema_ledger_matches_C': True, 'control_row': row,
                  'empty_new_work_tables': empty, 'physical_markers': markers,
                  'query_only': True, 'sql_changes': 0, 'network_requests': 0,
                  'reused_full_row_FK_proof': True}, indent=2))
