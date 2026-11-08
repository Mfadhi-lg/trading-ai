"""Capture quiescent production files through a RO mount; never recover/replay."""
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import sys
from pathlib import Path


def sha(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda: f.read(8 * 1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()


db = Path('/source/scraper.db')
files = [p for p in [db] + [Path(str(db)+s) for s in ('-wal','-shm','-journal')] if os.path.lexists(p)]
before = {p.name: {'bytes': p.stat().st_size, 'mtime_ns': p.stat().st_mtime_ns,
                   'inode': p.stat().st_ino, 'sha256': sha(p)} for p in files}
for p in files:
    if not stat.S_ISREG(p.lstat().st_mode):
        raise RuntimeError('Unexpected nonregular source file')
    dst = Path('/capture') / p.name
    with p.open('rb') as src, dst.open('xb') as target:
        shutil.copyfileobj(src, target, 8 * 1024 * 1024)
        target.flush()
        os.fsync(target.fileno())
    if sha(dst) != before[p.name]['sha256']:
        raise RuntimeError('Captured file differs from source')
    dst.chmod(0o444)
after = {p.name: {'bytes': p.stat().st_size, 'mtime_ns': p.stat().st_mtime_ns,
                  'inode': p.stat().st_ino, 'sha256': sha(p)} for p in files}
now_files = [p.name for p in [db] + [Path(str(db)+s) for s in ('-wal','-shm','-journal')] if os.path.lexists(p)]
if before != after or now_files != [p.name for p in files]:
    raise RuntimeError('Source changed during capture; retain evidence, stop')
result = {'status': 'PASS', 'files': before, 'source_unchanged_during_capture': True,
          'source_sqlite_opened': False, 'production_restore': False}
if len(files) == 1:
    conn = sqlite3.connect('file:/capture/scraper.db?mode=ro&immutable=1',uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    result['ledger'] = [dict(r) for r in conn.execute('SELECT * FROM schema_migrations ORDER BY version')]
    result['schema'] = [dict(r) for r in conn.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
    conn.close()
else:
    result['sql_inspection'] = 'Not performed; WAL/sidecar-aware inspection requires separate review, no replay'
print(json.dumps(result,indent=2,sort_keys=True))
