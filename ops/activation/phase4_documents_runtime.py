"""Exactly one retained issuer URL, then automatic artifact-scoped inbox handoff."""
from contextlib import ExitStack,closing
from datetime import datetime,timezone
import hashlib,json,signal,sqlite3,sys
from pathlib import Path
from types import SimpleNamespace
sys.path[:0]=['/activation','/app']


WRITE_TABLES={'response_blobs','artifact_download_attempts','artifact_download_attempt_sources',
        'artifact_contents','artifact_sources','document_artifacts','artifact_download_queue',
        'artifact_download_queue_attempts','artifact_download_host_health',
        'document_processing_inbox','document_processing_work','document_extraction_claims',
        'document_extractions','document_pages','document_page_elements','document_extraction_validations',
        'acquisition_host_state','acquisition_host_permits','acquisition_host_request_events'}


def fingerprint(c, table):
    """Hash rows in declared primary-key order, for both SQLite table kinds."""
    quote = lambda name: '"' + name.replace('"', '""') + '"'
    columns = c.execute('PRAGMA table_info(' + quote(table) + ')').fetchall()
    if not columns:
        raise ValueError('Unknown or empty table: ' + table)
    primary = [row[1] for row in sorted(columns, key=lambda row: row[5]) if row[5]]
    if primary:
        # Ordinary composite keys may allow duplicate NULL keys. Binary value
        # tie-breakers also keep case-insensitive key collations deterministic.
        nullable = any(not row[3] for row in columns if row[5])
        integer_key = len(primary) == 1 and next(row[2] for row in columns if row[5]).upper() == 'INTEGER'
        order = primary + ([row[1] for row in columns if row[1] not in primary] if nullable and not integer_key else [])
        ordering = ','.join(quote(name) + ' COLLATE BINARY' for name in order)
        if nullable and not integer_key:
            ordering += ',' + ','.join('typeof(' + quote(name) + ')' for name in order)
    else:
        # Ordinary tables without a declared PK still have a hidden row key.
        # Avoid a user column shadowing the chosen SQLite alias.
        names = {row[1].lower() for row in columns}
        alias = next((name for name in ('rowid', '_rowid_', 'oid') if name not in names), None)
        if alias is None:
            raise ValueError('No declared or accessible row key for table: ' + table)
        ordering = quote(alias)
    # Serialize in SQLite, avoiding millions of Python dicts/key sorts. Type
    # tags, exact binary bytes and round-trip real strings retain value identity.
    fields = []
    for row in sorted(columns, key=lambda row: row[1]):
        name = quote(row[1])
        key = "'" + row[1].replace("'", "''") + "'"
        value = "CASE typeof(" + name + ") WHEN 'real' THEN printf('%!.26g'," + name + ") WHEN 'blob' THEN hex(" + name + ") ELSE " + name + " END"
        fields.extend((key, 'json_array(typeof(' + name + '),' + value + ')'))
    serialized = 'json_object(' + ','.join(fields) + ')'
    h = hashlib.sha256()
    for row in c.execute('SELECT ' + serialized + ' FROM ' + quote(table) + ' ORDER BY ' + ordering):
        h.update(row[0].encode('utf-8'))
        h.update(b'\n')
    return h.hexdigest()


def assert_preserved(c, before):
    if before != {table: fingerprint(c, table) for table in before}:
        raise RuntimeError('Protected production data changed')



def assert_retry_admissible(c, plan, now):
    """Read-only admission of the exact frozen retry; never reset or defer rows."""
    from scrapper.db.host_permits import reviewed_policy, inherited_cooldown
    instant = datetime.fromisoformat(now)
    if instant.tzinfo is None:
        raise ValueError('Aware admission clock required')
    row = c.execute('SELECT * FROM artifact_download_queue WHERE download_job_id=?',
                    (plan['download_job_id'],)).fetchone()
    expected = plan.get('retry_state', {'status':'PENDING','attempt_count':0})
    if row is None or any(row[key] != value for key,value in expected.items()):
        raise RuntimeError('Selected queue retry state changed')
    if row['artifact_id'] != plan['artifact_id'] or row['requested_url'] != plan['url'] or row['scope_id'] != plan['scope_id']:
        raise RuntimeError('Selected source identity changed')
    if row['lease_owner'] is not None or row['lease_expires_at_utc'] is not None:
        raise RuntimeError('Selected queue has a lease')
    if row['next_attempt_at_utc'] and datetime.fromisoformat(row['next_attempt_at_utc']) > instant:
        raise RuntimeError('Selected queue backoff has not expired')
    if plan.get('previous_download_attempt_id'):
        previous = c.execute("""SELECT 1 FROM artifact_download_queue_attempts q
            JOIN artifact_download_attempts a USING(download_attempt_id)
            JOIN artifact_download_attempt_sources s USING(download_attempt_id)
            WHERE q.download_job_id=? AND a.download_attempt_id=? AND a.artifact_id=?
              AND a.requested_url=? AND s.artifact_source_id=?""",
            (plan['download_job_id'],plan['previous_download_attempt_id'],plan['artifact_id'],
             plan['url'],plan['previous_artifact_source_id'])).fetchone()
        if not previous:
            raise RuntimeError('Prior canonical attempt provenance changed')
    policy = reviewed_policy(c,plan['url'])
    if policy.blocked:
        raise RuntimeError('Source policy prohibits acquisition')
    if inherited_cooldown(c,policy.group) > instant.timestamp():
        raise RuntimeError('Publisher cooldown has not expired')
    state = c.execute('SELECT next_request_at,blocked_until,circuit_until FROM acquisition_host_state WHERE publisher_group=?',
                      (policy.group,)).fetchone()
    if state and max(state) > instant.timestamp():
        raise RuntimeError('Shared publisher backoff has not expired')
    if c.execute("SELECT 1 FROM acquisition_host_permits WHERE publisher_group=? AND status='ACTIVE' AND lease_expires_at>?",
                 (policy.group,instant.timestamp())).fetchone():
        raise RuntimeError('Publisher permit already active')
    if c.execute("SELECT 1 FROM artifact_download_queue WHERE target_host=? AND status='LEASED' AND lease_expires_at_utc>?",
                 (row['target_host'],now)).fetchone():
        raise RuntimeError('Publisher download already leased')
    return row


def main():
    from nse_results_runtime import run_owned,require
    from scrapper.operations.coordinator.exchange import ownership
    from scrapper.db.database import connect
    from scrapper.documents.store import assert_document_store_ready
    from scrapper.documents.downloader import DownloadPolicy
    from scrapper.documents.ocr import TesseractOcr
    from scrapper.pipeline.extract_document import processing_version
    from scrapper.pipeline import run_document_download_worker as hooks
    from scrapper.workflows.documents.download import run_document_download_worker
    from scrapper.operations.workers.document_processing import run_automatic_processing
    plan=json.loads(Path('/selected/plan.json').read_text())
    manifest=json.loads(Path('/selected/manifest.json').read_text())
    require(hashlib.sha256(Path('/selected/plan.json').read_bytes()).hexdigest()==manifest['plan_sha256'],'Plan drift')
    for name,digest in manifest['application_hashes'].items():
        require(hashlib.sha256((Path('/app')/name).read_bytes()).hexdigest()==digest,'Source drift: '+name)
    require(list(processing_version(TesseractOcr()).values())==plan['processing_version'],'Parser/OCR drift')
    require(Path('/documents/.trading_ai_document_store').is_file(),'Missing mounted sentinel')
    capacity=json.loads(Path('/selected/execution-storage-preflight.json').read_text())
    require(hashlib.sha256(Path('/physical_documents/.trading_ai_runtime_identity.json').read_bytes()).hexdigest()==capacity['physical_identity_sha256'],'Physical document identity drift')
    mounts=[line.split(' - ',1)[0].split() for line in Path('/proc/self/mountinfo').read_text().splitlines()]
    require(any(r[4]=='/documents' and 'rw' in r[5].split(',') for r in mounts),'Writable document bind mount absent')
    require(any(r[4]=='/physical_documents' and 'ro' in r[5].split(',') for r in mounts),'Physical read-only bind absent')
    db=Path('/data/scraper.db')
    def deadline(*_):raise TimeoutError('Bounded document canary deadline exceeded')
    signal.signal(signal.SIGALRM,deadline);signal.alarm(plan['deadline_seconds'])
    def guarded(path):
        require(Path(path)==db,'Unexpected DB target')
        c=connect(path)
        def authorizer(action,table,column,database,trigger):
            if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):
                if table not in WRITE_TABLES:return sqlite3.SQLITE_DENY
                if action==sqlite3.SQLITE_DELETE and table!='acquisition_host_permits':return sqlite3.SQLITE_DENY
            if action in (sqlite3.SQLITE_ATTACH,sqlite3.SQLITE_DETACH,sqlite3.SQLITE_CREATE_TABLE,sqlite3.SQLITE_ALTER_TABLE,sqlite3.SQLITE_DROP_TABLE):return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        c.set_authorizer(authorizer);return c
    with ExitStack() as stack:
        for role in ('scheduler','executor','company-worker','scope-monitor','periodic-executor','automatic-document-download'):
            require(stack.enter_context(ownership(db,role)),'Competing '+role)
        run_owned((),db,json.loads(Path('/activation/production-baseline.json').read_text()),verify_only=True)
        with closing(guarded(db)) as c:
            row=assert_retry_admissible(c,plan,datetime.now(timezone.utc).isoformat())
            require(row['artifact_id']==plan['artifact_id'] and row['requested_url']==plan['url'] and row['scope_id']==plan['scope_id'],'Identity changed')
            artifact=c.execute('SELECT company_id,artifact_type FROM document_artifacts WHERE artifact_id=?',(plan['artifact_id'],)).fetchone()
            require(artifact['company_id']==plan['company_id'] and artifact['artifact_type']==plan['artifact_type'],'Document/company identity changed')
            require(not c.execute('SELECT 1 FROM artifact_contents WHERE content_sha256=?',(plan['expected_sha256'],)).fetchone(),'Original already has canonical content; reconcile without downloading')
            require(c.execute("SELECT 1 FROM screener_collection_scope WHERE company_id=? AND eligibility='eligible'",(plan['company_id'],)).fetchone(),'Company no longer eligible')
            require(not c.execute('SELECT 1 FROM artifact_contents WHERE artifact_id=?',(plan['artifact_id'],)).fetchone(),'Selected content already exists')
            protected=('accepted_page_versions','financial_observations','exchange_source_access_events')
            before={t:fingerprint(c,t) for t in protected}
            counters={t:c.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in ('artifact_contents','document_processing_work','document_extractions')}
            print(json.dumps({'stage':'before','selected':dict(row),'storage':assert_document_store_ready().to_dict(),'protected':before}),flush=True)
            def checked_download(url,**kwargs):
                require(url==plan['url'],'Unexpected source URL')
                result=hooks.download_url(url,**kwargs)
                if result.is_validated:
                    require(result.byte_size==plan['expected_bytes'] and hashlib.sha256(result.body).hexdigest()==plan['expected_sha256'],'Source bytes changed; stop before persistence')
                return result
            bound=SimpleNamespace(**(vars(hooks)|{'download_url':checked_download}))
            assert_retry_admissible(c,plan,datetime.now(timezone.utc).isoformat())
            result=run_document_download_worker(c,limit=1,scope_id=plan['scope_id'],
                artifact_types=('transcript',),allowed_job_ids=(plan['download_job_id'],),max_attempts=3,
                shared_permits=True,policy=DownloadPolicy(max_bytes=plan['max_response_bytes'],max_redirects=0,total_timeout_seconds=45),_hooks=bound)
            print(json.dumps({'stage':'download','receipt':result}),flush=True)
            content=c.execute('SELECT c.*,b.local_path FROM artifact_contents c JOIN response_blobs b USING(blob_id) WHERE c.artifact_id=?',(plan['artifact_id'],)).fetchone()
            require(content is not None,'No saved validated content; processing not attempted')
            require(content['content_sha256']==plan['expected_sha256'],'Saved hash differs')
        receipt=run_automatic_processing(db_path=db,epoch=plan['epoch'],ocr_engine=TesseractOcr(),
            output_dir=Path('/data/exports/phase4-canary'),limit=1,historical_limit=1,
            artifact_ids=(plan['artifact_id'],),connection_factory=guarded)
        print(json.dumps({'stage':'processing','receipt':receipt}),flush=True)
        require(len(receipt.get('outcomes',[]))==1,'Automatic handoff did not process one item')
        require(receipt['outcomes'][0]['extraction_status']=='ok','Extraction did not succeed')
        with closing(guarded(db)) as c:
            assert_preserved(c, before)
            for t,n in counters.items():require(c.execute('SELECT count(*) FROM '+t).fetchone()[0]-n<=1,'Write count exceeded: '+t)
            pages=c.execute('SELECT count(*) FROM document_pages WHERE content_id=?',(content['content_id'],)).fetchone()[0]
            require(pages==plan['expected_pages'],'Unexpected extraction page count')
            require(hashlib.sha256(Path(content['local_path']).read_bytes()).hexdigest()==plan['expected_sha256'],'Stored original changed')
            print(json.dumps({'stage':'complete','content':dict(content),'pages':pages,'protected_unchanged':True,
                'queue':dict(c.execute('SELECT * FROM artifact_download_queue WHERE download_job_id=?',(plan['download_job_id'],)).fetchone())}),flush=True)
    signal.alarm(0)


if __name__=='__main__':main()
