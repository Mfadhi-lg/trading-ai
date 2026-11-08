"""One real dispatcher-admitted stage, using canonical queues and fences."""
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
import hashlib,json,signal,sqlite3,sys,time
sys.path[:0]=['/activation','/app']
from phase4_documents_runtime import WRITE_TABLES

ROUTINE_WRITE_TABLES=WRITE_TABLES|{'artifact_discovery_pages','artifact_events','artifact_expectations','artifact_check_events',
    'artifact_download_scopes','artifact_download_scope_companies','artifact_download_scope_artifacts',
    'document_links','document_link_promotions','fetch_observations','parse_artifacts','scrape_runs',
    'credit_rating_download_queue','credit_rating_resolution_attempts','credit_rating_resolution_steps',
    'credit_rating_host_health','artifact_content_relationships','html_document_renderings',
    'artifact_source_evidence_classifications'}


def routine_authorizer(action,table,column,database,trigger):
    """The actual production write boundary, also exercised by real fixture work."""
    if table=='artifact_period_corrections' and action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):
        return sqlite3.SQLITE_OK if action==sqlite3.SQLITE_INSERT else sqlite3.SQLITE_DENY
    recovery_columns={
        'artifact_recovery_candidates':{'content_class','updated_at_utc'},
        'artifact_recovery_targets':{'status','basic_rating_coverage','full_rationale_coverage',
            'resolved_candidate_id','lease_owner','lease_expires_at_utc','next_attempt_at_utc','updated_at_utc'}}
    if table in recovery_columns and action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE):
        return sqlite3.SQLITE_OK if action==sqlite3.SQLITE_UPDATE and column in recovery_columns[table] else sqlite3.SQLITE_DENY
    if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE) and table not in ROUTINE_WRITE_TABLES:return sqlite3.SQLITE_DENY
    if action==sqlite3.SQLITE_DELETE and table!='acquisition_host_permits':return sqlite3.SQLITE_DENY
    if action==sqlite3.SQLITE_UPDATE and table=='artifact_source_evidence_classifications' and column not in ('content_class','classification_basis_json','classified_at_utc'):return sqlite3.SQLITE_DENY
    if action in (sqlite3.SQLITE_ATTACH,sqlite3.SQLITE_DETACH,sqlite3.SQLITE_CREATE_TABLE,sqlite3.SQLITE_ALTER_TABLE,sqlite3.SQLITE_DROP_TABLE,sqlite3.SQLITE_CREATE_TRIGGER,sqlite3.SQLITE_DROP_TRIGGER,sqlite3.SQLITE_CREATE_VIEW,sqlite3.SQLITE_DROP_VIEW):return sqlite3.SQLITE_DENY
    if action==sqlite3.SQLITE_PRAGMA and column is not None and table.lower() in ('writable_schema','ignore_check_constraints','foreign_keys','trusted_schema'):
        if table.lower()!='foreign_keys' or str(column).lower() not in ('1','on'):return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK

from acquisition_failures import ExecutionDeadline as DeadlineExceeded


def main():
    from phase4_documents_runtime import WRITE_TABLES,fingerprint,assert_preserved
    from phase4_schedule import authorization_state
    timings={}
    total_started=time.monotonic()
    checkpoint=total_started
    def timing(name):
        nonlocal checkpoint
        current=time.monotonic();timings[name]=round(current-checkpoint,6);checkpoint=current
        print(json.dumps({'stage':'timing','operation':name,'seconds':timings[name]}),flush=True)
    from nse_results_runtime import run_owned,require
    from scrapper.db.database import connect,check_schema_compatibility
    from scrapper.operations.workers.automatic_documents import run_automatic_discovery,run_automatic_downloads,run_automatic_ratings
    from scrapper.operations.workers.document_processing import run_automatic_processing
    from scrapper.documents.ocr import TesseractOcr
    from scrapper.documents.downloader import DownloadPolicy
    from scrapper.documents.store import assert_document_store_ready
    from scrapper.sources.documents.routine import revision_patrol
    from scrapper.config import SNAPSHOTS
    plan=json.loads(Path('/selected/plan.json').read_text());manifest=json.loads(Path('/selected/manifest.json').read_text())
    require(plan['authorized'],'Routine production operation is not authorized')
    require(hashlib.sha256(Path('/selected/plan.json').read_bytes()).hexdigest()==manifest['plan_sha256'],'Plan drift')
    require(hashlib.sha256(Path(__file__).read_bytes()).hexdigest()==manifest['runtime_sha256'],'Runtime drift')
    for name,digest in manifest['application_hashes'].items():require(hashlib.sha256((Path('/app')/name).read_bytes()).hexdigest()==digest,'Source drift: '+name)
    now=datetime.now(timezone.utc)
    require(authorization_state(plan,now)=='RUNNING','Initial scheduling window expired')
    require(plan['historical_limit']==0 and plan['concurrency']==1,'Initial backlog or concurrency expansion')
    local_history=plan.get('local_historical_processing_limit',0)
    require(type(local_history) is int and local_history in (0,1),'Invalid local historical capacity')
    if local_history:
        require(plan['stage']=='DOCUMENT_PROCESSING' and plan.get('local_campaign_id')=='acquisition-recovery-20261026-one-local-document','Unscoped historical processing')
        from local_processing_network import assert_isolated
        print(json.dumps({'stage':'local_network_isolation','interfaces':assert_isolated()}),flush=True)
    capacity=json.loads(Path('/selected/execution-storage-preflight.json').read_text())
    require(hashlib.sha256(Path('/physical_documents/.trading_ai_runtime_identity.json').read_bytes()).hexdigest()==capacity['physical_identity_sha256'],'Physical document identity changed')
    mounts=[line.split(' - ',1)[0].split() for line in Path('/proc/self/mountinfo').read_text().splitlines()]
    require(any(r[4]=='/documents' and 'rw' in r[5].split(',') for r in mounts),'Document mount absent')
    require(any(r[4]=='/physical_documents' and 'ro' in r[5].split(',') for r in mounts),'Physical read-only mount absent')
    timing('configuration_and_source_attestation')
    db=Path('/data/scraper.db')
    run_owned((),db,json.loads(Path('/activation/production-baseline.json').read_text()),verify_only=True)
    timing('database_identity_schema_and_ownership')
    def guarded(path):
        require(Path(path)==db,'Unexpected DB target')
        c=connect(path)
        c.set_authorizer(routine_authorizer);return c
    def deadline(*_):raise DeadlineExceeded('Routine document stage deadline exceeded')
    signal.signal(signal.SIGALRM,deadline);signal.alarm(plan['runtime_deadline_seconds'])
    started=time.monotonic()
    with closing(guarded(db)) as c:
        check_schema_compatibility(c)
        # Every worker connection uses the deny-by-default SQL authorizer above.
        # Full fingerprints remain available for explicit audits, outside routine slots.
        protected={t:fingerprint(c,t) for t in ('accepted_page_versions','financial_observations','exchange_source_access_events')} if plan.get('full_preservation_audit',False) else None
        timing('database_open_and_preservation_guard')
        storage=assert_document_store_ready().to_dict()
        timing('storage_identity_capacity_and_write_probe')
    stage=plan['stage'];output=Path('/data/exports/phase4-routine')/now.strftime('%Y%m%dT%H%M%SZ')
    policy=DownloadPolicy(max_bytes=plan['max_response_bytes'],
        max_redirects=0 if plan.get('recovery_campaign') else plan['max_redirects'],total_timeout_seconds=45)
    if stage=='DOCUMENT_DISCOVERY':
        result=run_automatic_discovery(db_path=db,epoch=plan['epoch'],limit=plan['discovery_limit'],historical_limit=0,
            screener_request_limit=plan.get('screener_request_limit',2),
            official_request_limit=plan['official_request_limit'],
            output=output.with_suffix('.json'),snapshot_root=SNAPSHOTS,connection_factory=guarded)
    elif stage=='DOCUMENT_DOWNLOAD':
        recovery_ids=()
        if plan.get('recovery_campaign'):
            from scrapper.documents.recovery.finite_downloads import validate_campaign
            with closing(guarded(db)) as c:
                recovery_ids=validate_campaign(c,plan['recovery_campaign'],now=now.isoformat())
        from processing_pipeline import begin
        with begin(plan,output):
            result=run_automatic_downloads(db_path=db,epoch=plan['epoch'],limit=plan['download_limit'],historical_limit=0,
                policy=policy,max_attempts=3,connection_factory=guarded,recovery_job_ids=recovery_ids,
                saved_parse_ids=tuple(plan.get('saved_link_repair_ids',())))
    elif stage=='DOCUMENT_PROCESSING':
        if plan.get('local_resume_work_id'):
            require(local_history==1,'Historical resume lacks campaign authorization')
            from scrapper.operations.workers.document_processing import run_processing
            with closing(guarded(db)) as c:
                work=c.execute('SELECT content_id FROM document_processing_work WHERE work_id=?',(plan['local_resume_work_id'],)).fetchone()
                require(work is not None and work['content_id']==plan['local_resume_content_id'],'Historical resume identity differs')
            outcomes=run_processing(db_path=db,work_ids=[plan['local_resume_work_id']],ocr_engine=TesseractOcr(),output_dir=output,connection_factory=guarded)
            result={'status':'recorded','selected':[{'content_id':plan['local_resume_content_id'],'priority':3}],'outcomes':outcomes,'recovered_existing_work':True}
        else:
            result=run_automatic_processing(db_path=db,epoch=plan['epoch'],limit=plan['processing_limit'],historical_limit=local_history,
                ocr_engine=TesseractOcr(),output_dir=output,connection_factory=guarded,
                focus_company_ids=plan.get('focus_membership',{}).get('company_ids',[]))
    elif stage=='DOCUMENT_PATROL':
        with closing(guarded(db)) as c:result={'revalidations':revision_patrol(c,now=now.isoformat(),limit=1)}
    elif stage=='DOCUMENT_RATING':
        result=run_automatic_ratings(db_path=db,epoch=plan['epoch'],policy=policy,connection_factory=guarded,
            browser_request_limit=plan.get('rating_browser_request_limit',12))
    else:raise ValueError('Unknown routine document stage')
    timing('selected_stage_work')
    with closing(guarded(db)) as c:
        if protected is not None:assert_preserved(c,protected)
        counts={t:c.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in
            ('document_processing_inbox','document_processing_work','document_extractions')}
        queue=[dict(r) for r in c.execute('SELECT status,count(*) n FROM artifact_download_queue GROUP BY status')]
    timing('verification_and_queue_counts')
    signal.alarm(0)
    print(json.dumps({'stage':'document_stage_outcome','document_stage':stage,'result':result,
        'timings':timings,'total_runtime_seconds':round(time.monotonic()-total_started,6),
        'preservation_mode':'full_fingerprint' if protected is not None else 'sql_write_fence',
        'duration_seconds':round(time.monotonic()-started,3),'storage':storage,'queue_depth':queue,
        'processing_counts':counts,'protected_unchanged':True,'historical_limit':0,
        'local_historical_processing_limit':local_history,'finished_at_utc':datetime.now(timezone.utc).isoformat()}),flush=True)

if __name__=='__main__':
    from acquisition_failures import runtime_entrypoint
    sys.exit(runtime_entrypoint(main))
