"""Read exact bounded document attempts without source requests or queue changes."""
import json
from pathlib import Path
import sys
sys.path[:0] = ['/host', '/activation', '/app']
from document_audit_reader import observation, exact_ids
from document_backlog_audit import redact_tree


def inspect(conn, payload):
    ids=exact_ids(payload,10)
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
    return rows


def main(argv=None):
    payload=json.loads((sys.argv[1:] if argv is None else argv)[0])
    with observation(Path('/data/scraper.db')) as (conn, source):
        rows=inspect(conn,payload)
    print(json.dumps({'rows':redact_tree(rows),'source':source}))


if __name__=='__main__':main()
