"""Prepare by default; --execute is a later explicitly selected two-company run."""
import argparse
from datetime import datetime, timezone, timedelta
import fcntl
import hashlib
import hmac
import json
from pathlib import Path
import stat
import os
import subprocess

import nse_results as nse
from production_ownership import owner_gate

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / 'artifacts/step3-financial-readiness/prepared'
IMAGE = 'sha256:1a310808f878911b4bd0a324beaf2948f215e3743238c981a531656cf0eecbf9'


def save(name, payload):
    (OUT/name).write_text(json.dumps(payload,indent=2)+'\n')


def prepare(scope_monitor=False):
    nse.check_configuration()
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT/'execution-started.json').exists():
        raise RuntimeError('Selected execution was already started; preserve the package and inspect its outcome')
    plan={'mode':'current_financial_once','result_period':'2026-09','queue_item_ids':[18126,18127],
        'companies':[{'queue_item_id':18126,'path':'/company/GMBREW/','company_id':1,'exchange_filing_id':25717},
                     {'queue_item_id':18127,'path':'/company/TCS/','company_id':3,'exchange_filing_id':25715}],
        'max_companies':2,'runtime_deadline_seconds':600,'shared_host_permits':True,
        'existing_request_timeout_seconds':30,'existing_response_limit_bytes':8*1024*1024,
        'historical_claims_permitted':False,'automatic_retries':False,
        'source_session_validity':'not tested live; fail on rejected/login/captcha/401/403/429 response',
        'document_rating_queue_metadata':'canonical handoff retained; no downstream workers or downloads'}
    if scope_monitor:
        plan.update(mode='scope_monitor_once',queue_item_ids=[],companies=[],max_companies=0,
            max_scope_checks=1,selection='Existing monitor earliest due scope row; no financial queue claims')
    save('plan.json',plan)
    paths=('scrapper/config.py','scrapper/runtime_identity.py','scrapper/core/http.py',
        'scrapper/db/database.py','scrapper/db/screener_discovery_repo.py','scrapper/db/collection_scope_repo.py',
        'scrapper/db/screener/acceptance.py','scrapper/db/screener/observations.py',
        'scrapper/workflows/screener/company_worker.py','scrapper/workflows/screener/ingest.py',
        'scrapper/operations/workers/screener_requests.py','scrapper/operations/coordinator/exchange.py',
        'scrapper/commands/collection_scope.py','scrapper/core/collection_scope.py',
        'scrapper/sources/screener/document_links.py',
        'scrapper/pipeline/accepted_document_downloads.py',
        'scrapper/documents/discovery/promotion.py','scrapper/db/document_download_queue_repo.py')
    source=json.loads(nse.SOURCE.read_text())
    hashes={r['path']:r['sha256'] for r in source['files']}
    overlays=('scrapper/runtime_identity.py','scrapper/db/screener_discovery_repo.py','scrapper/commands/collection_scope.py',
        'scrapper/config.py','scrapper/sources/screener/document_links.py',
        'scrapper/workflows/screener/ingest.py',
        'scrapper/pipeline/accepted_document_downloads.py',
        'scrapper/documents/discovery/promotion.py','scrapper/db/document_download_queue_repo.py')
    for path in paths:
        if path in overlays:continue
        if path == 'scrapper/workflows/screener/ingest.py':
            # Financial work reads this file from the verified image, not the
            # Phase 4 document-only host implementation. Its image hash remains
            # in application_hashes and is checked inside the container.
            scheduling=json.loads((HERE/'automatic-acquisition-manifest.json').read_text())
            nse.require(hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==scheduling['files'][path],
                        'Sealed document-only ingestion source changed: '+path)
            continue
        nse.require(hashlib.sha256((ROOT/path).read_bytes()).hexdigest() == hashes[path],
                    'Financial application differs from existing verified image: '+path)
    save('manifest.json',{'image':IMAGE,'application_hashes':{p:hashes[p] for p in paths if p not in overlays},
        'application_overlays':{p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in overlays},
        'overlay_base_hashes':{p:hashes[p] for p in overlays},
        'operator_hashes':{p:hashlib.sha256((HERE/p).read_bytes()).hexdigest() for p in
            ('paired_downloads.py','paired_download_runtime.py','processing_pipeline.py','financial_priority.py','financial_screen_refresh.py','step3_financial_runtime.py','nse_results_runtime.py','production_ownership.py','sqlite_sidecar_guard.py')},
        'plan_sha256':hashlib.sha256((OUT/'plan.json').read_bytes()).hexdigest(),
        'production_execution_authorized_by_preparation':False,'image_rebuild':False,
        'migration_hashes':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'scrapper/db/migrations').glob('*.sql')}})
    from acquisition_release import workspace_root
    cookie=workspace_root(ROOT)/'.screener_session'
    nse.require(cookie.is_file() and not cookie.is_symlink() and cookie.stat().st_size>0,
                'Existing private Screener session file is required')
    nse.require(stat.S_IMODE(cookie.stat().st_mode) & 0o077 == 0,'Screener session file must remain private')
    compose=json.loads((HERE/'compose.nse.json').read_text())
    service=compose['services'].pop('nse-results')
    compose['services']={'current-financial-once':service}
    compose['name']='trading-ai-current-financial-once'
    service.update(image=IMAGE,profiles=['current-financial'],entrypoint=['python','/activation/step3_financial_runtime.py'])
    for mount in service['volumes']:
        if mount['type']=='bind':mount['source']=str(HERE)
    service['volumes'].append({'type':'bind','source':str(OUT),'target':'/selected','read_only':True})
    for path in overlays:
        service['volumes'].append({'type':'bind','source':str(ROOT/path),'target':'/app/'+path,'read_only':True,'bind':{'create_host_path':False}})
    service['volumes'].append({'type':'bind','source':str(ROOT/'scrapper/db/migrations'),'target':'/app/scrapper/db/migrations','read_only':True,'bind':{'create_host_path':False}})
    service['environment']['SCREENER_SESSION_FILE']='/run/secrets/screener_session'
    service['secrets']=[{'source':'screener_session','target':'screener_session'}]
    compose['secrets']={'screener_session':{'file':str(cookie)}}
    identity=json.loads((HERE/'runtime-identity-nse-results.json').read_text())
    identity.update(image_id=IMAGE,release_id='current-financial-selected-20261009')
    save('identity.json',identity)
    for name,path,role in (('store','/data','store'),('snapshots','/data/snapshots','documents')):
        save(name+'.json',{'environment':'production','path':path,'release_id':identity['release_id'],'roles':[role]})
    compose['configs']={'nse_identity':{'file':str(OUT/'identity.json')},
        'nse_store':{'file':str(OUT/'store.json')},'nse_snapshots':{'file':str(OUT/'snapshots.json')}}
    save('compose.json',compose)
    return plan


def main():
    global OUT
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--preflight',action='store_true')
    parser.add_argument('--scope-monitor',action='store_true',help='Prepare one existing market-cap monitor check; no financial company work')
    parser.add_argument('--retry-updated-session',action='store_true',help='One explicitly authorized retry of the same two items; preserve original run evidence')
    args=parser.parse_args()
    if args.scope_monitor:
        OUT=ROOT/'artifacts/step3-financial-readiness/prepared-scope-monitor'
    if args.retry_updated_session:
        nse.require(not args.scope_monitor,'Financial retry cannot include the scope monitor')
        original=ROOT/'artifacts/step3-financial-readiness/prepared'
        nse.require(json.loads((original/'outcome.json').read_text())['status']=='STOP', 'Retry requires the retained failed canary')
        OUT=ROOT/'artifacts/step3-financial-readiness/retry-updated-session-20261009'
    plan=prepare(scope_monitor=args.scope_monitor)
    if args.retry_updated_session:
        nonce=os.urandom(32)
        proof={'nonce':nonce.hex(),'file_hmac':hmac.new(nonce,(ROOT/'.screener_session').read_bytes(),hashlib.sha256).hexdigest()}
        save('session-check.json',proof);os.chmod(OUT/'session-check.json',0o600)
    if not args.execute and not args.preflight:
        print(json.dumps({'status':'PREPARED_NOT_EXECUTED','plan':plan,'package':str(OUT)}))
        return 0
    with (HERE/'.nse-owner.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if args.execute:
            from scrapper.sources.exchange.reporting_periods import IST
            now=datetime.now(timezone.utc)
            local=now.astimezone(IST)
            slots=[]
            for offset in (0,1):
                day=local.date()+timedelta(days=offset)
                times=[(9,0),(18,0)]+([(19,30)] if day.weekday()<6 else [])
                for hour,minute in times:
                    slot=datetime(day.year,day.month,day.day,hour,minute,tzinfo=IST)
                    if slot>=local:slots.append(slot)
            soonest=min(slots)
            nse.require((soonest-now).total_seconds()>780,
                'Reserved exchange slot is within 13 minutes; defer this canary until after that slot settles')
        gate=owner_gate()
        image=json.loads(nse.checked(['docker','image','inspect',IMAGE]))[0]
        nse.require(image['Id']==IMAGE,'Existing financial application image changed')
        volume=json.loads(nse.checked(['docker','volume','inspect',nse.VOLUME]))[0]
        nse.require(volume['Labels'].get('com.docker.compose.project')=='trading-ai','Production volume identity changed')
        command=['docker','compose','-f',str(OUT/'compose.json'),'--profile','current-financial',
            'run','--rm','--no-deps','--name','trading-ai-current-financial-once','current-financial-once']
        if args.preflight:
            command.append('--verify-only')
        else:
            save('execution-started.json',{'started_at_utc':datetime.now(timezone.utc).isoformat(),
                'owner_gate':gate,'plan':plan,'automatic_retry':False})
        log_name='preflight.log' if args.preflight else 'runtime.log'
        with (OUT/log_name).open('w') as log:
            result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT)
        save('preflight-outcome.json' if args.preflight else 'outcome.json',
            {'status':'PASS' if not result.returncode else 'STOP','exit_code':result.returncode,
             'production_execution':not args.preflight,'owner_after':owner_gate(),'automatic_retry':False})
        print(json.dumps({'status':'PASS' if not result.returncode else 'STOP','log':str(OUT/log_name)}))
        return result.returncode


if __name__=='__main__':
    raise SystemExit(main())
