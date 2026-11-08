"""Networkless, immutable read of exact bounded download attempt evidence."""
import fcntl
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0,'/activation')
from sqlite_sidecar_guard import _assert_safe_empty_wal,snapshot

ids=json.loads(sys.argv[1])
if not 1<=len(ids)<=10 or len(set(ids))!=len(ids) or any(type(i) is not int or i<1 for i in ids):
    raise ValueError('Exact one-to-ten job identities required')
db=Path('/data/scraper.db')
with Path('/activation/.monitor-readers.lock').open('rb') as gate:
    fcntl.flock(gate,fcntl.LOCK_EX)
    before=_assert_safe_empty_wal(db)
    conn=sqlite3.connect('file:'+str(db)+'?mode=ro&immutable=1',uri=True)
    conn.row_factory=sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    try:
        marks=','.join('?' for _ in ids)
        rows=[dict(r) for r in conn.execute('''SELECT q.download_job_id,q.status,
            a.download_attempt_id,a.result_status,a.fetched_at_utc,a.byte_size,
            a.content_sha256,b.local_path FROM artifact_download_queue q
            JOIN artifact_download_queue_attempts qa USING(download_job_id)
            JOIN artifact_download_attempts a USING(download_attempt_id)
            JOIN response_blobs b ON b.blob_id=a.blob_id
            WHERE q.download_job_id IN ('''+marks+''') AND a.download_attempt_id=(
                SELECT max(x.download_attempt_id) FROM artifact_download_queue_attempts x
                WHERE x.download_job_id=q.download_job_id)''',ids)]
    finally:conn.close()
    if snapshot(db)!=before['files']:raise RuntimeError('DB changed during immutable verification')
print(json.dumps({'rows':rows}))
