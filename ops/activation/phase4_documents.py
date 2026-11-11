"""Prepare a one-document production canary. Execution requires --execute."""
import argparse,fcntl,hashlib,json,plistlib,subprocess
from datetime import datetime,timezone
from pathlib import Path
from production_ownership import owner_gate

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
OUT=ROOT/'artifacts/phase4/canary'
STORE=Path('/Volumes/My Passport/passport/trading-ai')
from postgresql_release import configured_image
IMAGE='sha256:1a310808f878911b4bd0a324beaf2948f215e3743238c981a531656cf0eecbf9'
IMAGE=configured_image(IMAGE)
UUID='60E1204B-CB98-48C4-9398-1D2FBAC9B44A'


def save(name,value): (OUT/name).write_text(json.dumps(value,indent=2)+'\n')
def command(args): return subprocess.check_output(args,text=True,timeout=60)

def storage_preflight():
    import os
    if not os.path.ismount('/Volumes/My Passport'):
        raise RuntimeError('External document volume is not mounted')
    p=plistlib.loads(subprocess.check_output(['diskutil','info','-plist','/Volumes/My Passport'],timeout=30))
    if p['VolumeUUID']!=UUID or p['MountPoint']!='/Volumes/My Passport' or p['Internal']:
        raise RuntimeError('External drive identity changed')
    if not (STORE/'.trading_ai_document_store').is_file(): raise RuntimeError('Document sentinel missing')
    expected={'environment':'production','path':'/documents','release_id':'c05.3-release-20261003','roles':['documents']}
    if json.loads((STORE/'.trading_ai_runtime_identity.json').read_text())!=expected:
        raise RuntimeError('Physical document identity changed')
    total=p['APFSContainerSize'];free=p['APFSContainerFree'];reserve=max(100*1024**3,int(total*.15))
    if free-reserve<10*1024**2: raise RuntimeError('Physical reserve cannot cover canary')
    # Verify four retained originals, without traversing or rewriting archive.
    snapshot=json.loads((ROOT/'artifacts/phase4/inspection/20261011T150755/production-inspection.json').read_text())
    checked=[]
    for c in snapshot['saved_candidates']:
        path=STORE/c['local_path'].removeprefix('/documents/')
        if path.stat().st_size!=c['byte_size'] or hashlib.sha256(path.read_bytes()).hexdigest()!=c['content_sha256']:
            raise RuntimeError('Archive hash or size changed')
        checked.append({'content_id':c['content_id'],'sha256':c['content_sha256'],'bytes':c['byte_size']})
    return {'root':str(STORE),'volume_uuid':UUID,'total_bytes':total,'free_bytes':free,
        'required_free_bytes':reserve,'measured_at_utc':datetime.now(timezone.utc).isoformat(),
        'archive_checked':checked,'capacity_source':'diskutil APFS physical container',
        'physical_identity_sha256':hashlib.sha256((STORE/'.trading_ai_runtime_identity.json').read_bytes()).hexdigest()}


def prepare(retry=False):
    OUT.mkdir(parents=True,exist_ok=True)
    if (OUT/'execution-started.json').exists(): raise RuntimeError('Canary already attempted; preserve receipt')
    save('storage-preflight.json',storage_preflight())
    image=json.loads(command(['docker','image','inspect',IMAGE]))[0]
    if image['Id']!=IMAGE: raise RuntimeError('Application image changed')
    version=json.loads(command(['docker','run','--rm','--network','none','--read-only',
        '--env','PYTHONDONTWRITEBYTECODE=1','--entrypoint','python',IMAGE,'-c',
        'import json; from scrapper.documents.ocr import TesseractOcr; from scrapper.pipeline.extract_document import processing_version; print(json.dumps(processing_version(TesseractOcr()).values()))']))
    plan={'artifact_id':81417,'company_id':4320,'download_job_id':144466,
        'scope_id':'document-acquisition-full-v1','artifact_type':'transcript',
        'url':'https://www.sacheerome.com/pdf/H2_FY_26_Earning_Call_Transcript.pdf',
        'expected_sha256':'f6101da1213c2893484b94861f85e66375726e12b78a313b97298c63586ca3a3',
        'expected_bytes':634502,'expected_pages':21,'source_period_identity':'H2 FY26',
        'publication_date':'2026-06-01','period_end_mutation':False,
        'epoch':'2026-10-09T00:00:00+00:00','historical_limit':1,'max_downloads':1,'max_extractions':1,
        'max_response_bytes':10485760,'max_redirects':0,'max_attempts_this_run':1,
        'deadline_seconds':300,'processing_version':version,
        'new_artifacts':0,'new_scopes':0,'new_queue_jobs':0,
        'max_new_contents':1,'max_new_processing_work':1,'max_new_extractions':1,
        'financial_writes':0,'rating_or_recording_work':0,'source_requests':1,
        'authorization':'PENDING; preparation never authorizes execution'}
    if retry:
        previous=json.loads((ROOT/'artifacts/phase4/canary-attempt-02/verification/production-inspection.json').read_text())
        selected=previous['queue'][0]
        if selected['download_job_id']!=144466 or selected['status']!='RETRYABLE_FAILURE' or selected['attempt_count']!=1:
            raise RuntimeError('Retained canonical retry evidence does not match scope')
        plan.update(retry_state={key:selected[key] for key in ('status','attempt_count','next_attempt_at_utc','claim_token','last_result_status')},
            previous_download_attempt_id=84702,previous_artifact_source_id=101555)
    save('plan.json',plan)
    identity=json.loads((HERE/'runtime-identity-nse-results.json').read_text())
    identity.update(authorized=False,image_id=IMAGE)
    identity['targets']['documents']='/documents'
    save('runtime-identity.json',identity)
    doc={'environment':'production','path':'/documents','release_id':identity['release_id'],'roles':['documents']}
    save('documents-sentinel.json',doc)
    compose=json.loads((HERE/'compose.nse.json').read_text()); service=compose['services'].pop('nse-results')
    compose['name']='trading-ai-phase4-canary';compose['services']={'document-canary':service}
    service.update(image=IMAGE,profiles=['canary'],entrypoint=['python','/activation/phase4_documents_runtime.py'])
    for m in service['volumes']:
        if m['type']=='bind':m['source']=str(HERE)
    service['volumes'] += [
        {'type':'bind','source':str(ROOT/'scrapper'),'target':'/app/scrapper','read_only':True,'bind':{'create_host_path':False}},
        {'type':'bind','source':str(OUT),'target':'/selected','read_only':True},
        {'type':'bind','source':str(STORE),'target':'/documents','bind':{'create_host_path':False}},
        {'type':'bind','source':str(STORE),'target':'/physical_documents','read_only':True,'bind':{'create_host_path':False}}]
    service['environment'].update(SCRAPER_DOCUMENT_STORE_PATH='/documents',PYTHONDONTWRITEBYTECODE='1',
        DOCUMENT_REQUIRE_SENTINEL='1',DOCUMENT_STORE_REQUIRE_CAPACITY_FILE='1',DOCUMENT_STORE_CAPACITY_MAX_AGE_SECONDS='600')
    compose['configs']['nse_identity']['file']=str(OUT/'runtime-identity.json')
    for name in ('nse_store','nse_snapshots'):
        compose['configs'][name]['file']=str((HERE/compose['configs'][name]['file']).resolve())
    compose['configs']['documents_identity']={'file':str(OUT/'documents-sentinel.json')}
    service['configs'].append({'source':'documents_identity','target':'/documents/.trading_ai_runtime_identity.json'})
    save('compose.json',compose)
    files=[p for p in (ROOT/'scrapper').rglob('*.py') if '.venv' not in p.parts]
    files.extend((ROOT/'scrapper/documents/download/certificates').glob('*.pem'))
    save('manifest.json',{'image':IMAGE,'application_hashes':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
        'runtime_sha256':hashlib.sha256((HERE/'phase4_documents_runtime.py').read_bytes()).hexdigest(),
        'plan_sha256':hashlib.sha256((OUT/'plan.json').read_bytes()).hexdigest()})
    return plan


def main():
    global OUT
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--execute',action='store_true')
    p.add_argument('--retry-state',action='store_true',help='Prepare the exact retained attempt1 retry, without resetting it')
    p.add_argument('--attempt-dir',type=Path,default=OUT,help='Separate receipt directory; never reuse a consumed attempt')
    a=p.parse_args();OUT=a.attempt_dir.resolve()
    with (HERE/'.nse-owner.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        before=owner_gate()
        if not a.execute:
            print(json.dumps({'status':'PREPARED','production_document_writes':0,'plan':prepare(retry=a.retry_state)}));return
        # Exact prepared package only. Changing source requires another report.
        plan=json.loads((OUT/'plan.json').read_text());m=json.loads((OUT/'manifest.json').read_text())
        if (OUT/'execution-started.json').exists():raise RuntimeError('Canary already attempted')
        for name,digest in m['application_hashes'].items():
            if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=digest:raise RuntimeError('Source changed: '+name)
        if hashlib.sha256((OUT/'plan.json').read_bytes()).hexdigest()!=m['plan_sha256']:raise RuntimeError('Plan changed')
        if hashlib.sha256((HERE/'phase4_documents_runtime.py').read_bytes()).hexdigest()!=m['runtime_sha256']:raise RuntimeError('Runtime changed')
        capacity=storage_preflight();save('execution-storage-preflight.json',capacity)
        identity=json.loads((OUT/'runtime-identity.json').read_text());identity['authorized']=True;save('runtime-identity.json',identity)
        save('execution-started.json',{'at':datetime.now(timezone.utc).isoformat(),'owner':before})
        # Existing capacity publisher; one authorized metadata write on the drive.
        temporary=STORE/'.phase4-capacity.tmp'
        if temporary.exists():raise RuntimeError('Unexpected capacity temporary file')
        import os
        with temporary.open('x') as stream:
            json.dump(capacity,stream);stream.flush();os.fsync(stream.fileno())
        temporary.replace(STORE/'.trading_ai_document_store_capacity.json')
        args=['docker','compose','-f',str(OUT/'compose.json'),'--profile','canary','run','--rm','--no-deps','document-canary']
        try:
            r=subprocess.run(args,capture_output=True,text=True,timeout=360)
            (OUT/'runtime.log').write_text(r.stdout);(OUT/'runtime.stderr').write_text(r.stderr)
            save('outcome.json',{'exit_code':r.returncode,'owner_after':owner_gate(),'finished_at_utc':datetime.now(timezone.utc).isoformat()})
            print(json.dumps({'exit_code':r.returncode,'receipt':str(OUT/'runtime.log')}))
            raise SystemExit(r.returncode)
        except subprocess.TimeoutExpired:
            save('outcome.json',{'status':'TIMEOUT; inspect container and durable claims before retry'})
            raise


if __name__=='__main__':main()
