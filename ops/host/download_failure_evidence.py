"""Read exact selected attempt errors without source requests or queue changes."""
import fcntl,json,sqlite3,sys
from pathlib import Path
sys.path.insert(0,'/activation')
from sqlite_sidecar_guard import _assert_safe_empty_wal,snapshot

payload=json.loads(sys.argv[1])
ids=payload if isinstance(payload,list) else []
if isinstance(payload,list) and (not ids or len(ids)>20 or any(type(i)is not int or i<1 for i in ids)):
    raise ValueError('Bounded exact job IDs required')
db=Path('/data/scraper.db')
with Path('/activation/.monitor-readers.lock').open('rb') as gate:
    fcntl.flock(gate,fcntl.LOCK_EX)
    before=_assert_safe_empty_wal(db)
    conn=sqlite3.connect('file:'+str(db)+'?mode=ro&immutable=1',uri=True);conn.row_factory=sqlite3.Row
    conn.execute('PRAGMA query_only=ON')
    try:
        if isinstance(payload,dict):
            rows=[]
            for sql,params in payload['queries']:
                wrapped='SELECT q.target_host,count(*) jobs FROM ('+sql+') selected JOIN artifact_download_queue q USING(download_job_id) GROUP BY q.target_host'
                rows.extend(dict(r) for r in conn.execute(wrapped,params))
        else:
            marks=','.join('?' for _ in ids)
            rows=[dict(r) for r in conn.execute('''SELECT q.download_job_id,q.requested_url,q.target_host,
            q.status,q.next_attempt_at_utc,q.last_error,a.result_status,a.http_status,a.final_url,
            a.response_headers_json,a.byte_size,a.error_json,a.limits_json,a.elapsed_ms
            FROM artifact_download_queue q LEFT JOIN artifact_download_queue_attempts qa
            ON qa.download_job_id=q.download_job_id AND qa.download_attempt_id=(
              SELECT max(x.download_attempt_id) FROM artifact_download_queue_attempts x WHERE x.download_job_id=q.download_job_id)
            LEFT JOIN artifact_download_attempts a USING(download_attempt_id)
            WHERE q.download_job_id IN ('''+marks+')',ids)]
    finally:conn.close()
    if snapshot(db)!=before['files']:raise RuntimeError('DB changed during read-only inspection')
print(json.dumps({'rows':rows}))
