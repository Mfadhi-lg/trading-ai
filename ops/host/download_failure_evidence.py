"""Read exact bounded document attempts without source requests or queue changes."""
import json
from pathlib import Path
import sys
sys.path[:0] = ['/host', '/activation', '/app']
from document_audit_reader import observation, exact_ids
from document_backlog_audit import redact_tree


def inspect(conn, payload):
    ids=exact_ids(payload,20) if not isinstance(payload,dict) else []
    if isinstance(payload,dict):
        queries=payload.get('queries')
        if not isinstance(queries,list) or not 1<=len(queries)<=20:
            raise ValueError('Bounded diagnostic queries required')
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
    return rows


def main(argv=None):
    payload=json.loads((sys.argv[1:] if argv is None else argv)[0])
    with observation(Path('/data/scraper.db')) as (conn, source):
        rows=inspect(conn,payload)
    print(json.dumps({'rows':redact_tree(rows),'source':source}))


if __name__=='__main__':main()
