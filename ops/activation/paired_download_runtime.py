"""Fenced download child; only its supervisor performs cold-start verification."""
def main():
    import sys,os,ctypes,signal,json,hashlib
    from pathlib import Path
    from datetime import datetime,timezone
    sys.path[:0]=['/document_app','/activation']
    parent=int(os.environ['PAIRED_PARENT_PID'])
    libc=ctypes.CDLL(None,use_errno=True)
    if libc.prctl(1,signal.SIGKILL,0,0,0)!=0 or os.getppid()!=parent:
        raise RuntimeError('Paired supervisor ownership lost')
    plan=json.loads(Path('/selected/plan.json').read_text());manifest=json.loads(Path('/selected/manifest.json').read_text())
    if hashlib.sha256(Path('/selected/plan.json').read_bytes()).hexdigest()!=manifest['plan_sha256']:raise RuntimeError('Paired plan drift')
    pair=plan['paired_downloads']
    if not plan.get('scheduled') or not 1<=pair['limit']<=30:raise RuntimeError('Unauthorized paired download scope')
    for name,digest in manifest['paired_application_hashes'].items():
        if hashlib.sha256((Path('/document_app')/name).read_bytes()).hexdigest()!=digest:raise RuntimeError('Paired application drift: '+name)
    from scrapper.db.database import connect,check_schema_compatibility
    from scrapper.operations.workers.automatic_documents import run_automatic_downloads
    from scrapper.documents.downloader import DownloadPolicy
    from phase4_routine_runtime import routine_authorizer
    db=Path('/data/scraper.db')
    capacity=json.loads(Path('/selected/paired-capacity.json').read_text())
    if hashlib.sha256(Path('/physical_documents/.trading_ai_runtime_identity.json').read_bytes()).hexdigest()!=capacity['physical_identity_sha256']:
        raise RuntimeError('Physical document identity changed')
    def guarded(path):
        if Path(path)!=db:raise RuntimeError('Unexpected paired DB target')
        c=connect(path);check_schema_compatibility(c);c.set_authorizer(routine_authorizer);return c
    from acquisition_failures import ExecutionDeadline
    def deadline(*args):raise ExecutionDeadline('Paired download deadline exceeded')
    signal.signal(signal.SIGALRM,deadline);signal.alarm(450)
    started=datetime.now(timezone.utc).isoformat()
    print(json.dumps({'stage':'paired_download_started','at':started,'supervisor_pid':parent}),flush=True)
    from processing_pipeline import begin
    with begin(plan,Path('/data/exports/paired-downloads')/pair['id']) as processing:
        result=run_automatic_downloads(db_path=db,epoch=pair['epoch'],limit=pair['limit'],historical_limit=0,
            connection_factory=guarded,policy=DownloadPolicy(max_bytes=10*1024**2,max_redirects=1,total_timeout_seconds=45),max_attempts=3)
    record={'stage':'document_stage_outcome','document_stage':'DOCUMENT_DOWNLOAD','paired_with':'FINANCIAL',
        'started_at_utc':started,'finished_at_utc':datetime.now(timezone.utc).isoformat(),'result':result}
    if processing is not None:record['processing_companion']=processing.record
    target=Path('/data/exports/paired-downloads')/(pair['id']+'.json');temporary=target.with_suffix('.tmp')
    temporary.write_text(json.dumps(record)+'\n');os.replace(temporary,target)
    print(json.dumps(record),flush=True)

if __name__=='__main__':
    from acquisition_failures import runtime_entrypoint
    raise SystemExit(runtime_entrypoint(main))
