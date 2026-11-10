"""Evidence-backed same-URL revalidation using existing scopes and queues.

A changed indicator permits a bounded GET, never proves changed contents.
The canonical content hash decides whether a new inbox/version is created.
"""
from ..db.repository_backend import statement, is_postgresql, execute, generated_id, atomic_repository, begin_immediate
import hashlib
import json
from urllib.parse import urlsplit
from ..core.access_policy import is_blocked_terms_target


@atomic_repository('document-revision')
def enqueue_revision(conn, *, artifact_id, url, indicator, now):
    if not indicator or not isinstance(indicator, dict):
        raise ValueError('Revision requires source metadata or validator evidence')
    if not set(indicator) <= {'publication', 'title', 'etag', 'last-modified', 'bounded_hash_check'}:
        raise ValueError('Unsupported revision indicator')
    host = (urlsplit(url).hostname or '').lower()
    if not host or host == 'api.bseindia.com' or is_blocked_terms_target(host):
        return {'status':'source_access_blocked'}
    artifact = conn.execute('SELECT * FROM document_artifacts WHERE artifact_id=?', (artifact_id,)).fetchone()
    source = conn.execute('SELECT 1 FROM artifact_sources WHERE artifact_id=? AND document_url=?', (artifact_id,url)).fetchone()
    if not artifact or not source or not conn.execute('SELECT 1 FROM artifact_contents WHERE artifact_id=?',(artifact_id,)).fetchone():
        raise ValueError('Revision must retain an existing artifact, source and original content')
    policy = {'revision_artifact_id':artifact_id, 'revision_url':url, 'revision_indicator':indicator}
    encoded = json.dumps(policy,sort_keys=True,separators=(',',':'))
    scope = 'document-revision-' + hashlib.sha256(encoded.encode()).hexdigest()
    with conn:
        conn.execute(statement(conn, '''INSERT OR IGNORE INTO artifact_download_scopes
            VALUES (?, 'custom', 'Evidence-backed immutable document revision', ?, ?, ?, ?)''', "INSERT INTO artifact_download_scopes\n            VALUES (?, 'custom', 'Evidence-backed immutable document revision', ?, ?, ?, ?) ON CONFLICT DO NOTHING"),
            (scope,json.dumps([artifact['artifact_type']]),encoded,now,now))
        conn.execute(statement(conn, 'INSERT OR IGNORE INTO artifact_download_scope_companies(scope_id,company_id,symbol) VALUES (?,?,?)', 'INSERT INTO artifact_download_scope_companies(scope_id,company_id,symbol) VALUES (?,?,?) ON CONFLICT DO NOTHING'),
            (scope,artifact['company_id'],f"company:{artifact['company_id']}"))
        conn.execute(statement(conn, 'INSERT OR IGNORE INTO artifact_download_scope_artifacts VALUES (?,?)', 'INSERT INTO artifact_download_scope_artifacts VALUES (?,?) ON CONFLICT DO NOTHING'),(scope,artifact_id))
        # One URL only. Never rearm or mutate a succeeded/terminal canonical job.
        conn.execute(statement(conn, '''INSERT OR IGNORE INTO artifact_download_queue
            (artifact_id,requested_url,target_host,priority,scope_id,status,created_at_utc,updated_at_utc)
            VALUES (?,?,?,20,?,'PENDING',?,?)''', "INSERT INTO artifact_download_queue\n            (artifact_id,requested_url,target_host,priority,scope_id,status,created_at_utc,updated_at_utc)\n            VALUES (?,?,?,20,?,'PENDING',?,?) ON CONFLICT DO NOTHING"),(artifact_id,url,host,scope,now,now))
    row=conn.execute('SELECT download_job_id,status FROM artifact_download_queue WHERE scope_id=?',(scope,)).fetchone()
    return dict(row)
