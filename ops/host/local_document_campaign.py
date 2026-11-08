"""Freeze and execute the separately authorized finite Phase 2 local pilot."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import time


def remaining_contents(campaign,out,checksum):
    remaining=[]
    for item in campaign['contents']:
        path=out/'receipts'/('content-%s.json'%item['content_id'])
        if not path.exists():remaining.append(item['content_id']);continue
        receipt=json.loads(path.read_text())
        if (receipt.get('campaign_sha256')!=checksum or receipt.get('content_id')!=item['content_id']
            or type(receipt.get('extraction_id')) is not int or receipt['extraction_id']<1
            or receipt.get('extraction_status') not in ('ok','partial','failed')):
            raise RuntimeError('Completion receipt identity differs')
    return remaining


def freeze(root, out):
    from automatic_acquisition import save
    from scrapper.operations.workers.local_document_campaign import digest
    manifest_path=out/'PILOT_AUTHORIZATION_AND_PLAN.json'
    if manifest_path.exists():raise RuntimeError('Finite manifest already frozen; never replace it')
    raw=out/'raw';inventory=json.loads((raw/'inventory.json').read_text())
    verified={r['content_id']:r for r in map(json.loads,(raw/'verification.jsonl').read_text().splitlines())}
    contents=[json.loads(line) for line in (raw/'contents.jsonl').read_text().splitlines()]
    selected=[];hashes=set();diversity=set()
    for kind,target in (('credit_rating',35),('presentation',35),('transcript',30)):
        candidates=[r for r in contents if r['artifact_type']==kind and r['supported'] and r['eligible']
            and r['extraction_status']=='not_started' and r['exact_cache'] is None
            and verified.get(r['content_id'],{}).get('state')=='verified']
        # Deterministic diversity across publisher, fiscal quarter and size;
        # unknown period stays unknown. Prefer recent publications only when known.
        candidates.sort(key=lambda r:(r['event_date'] or '',r['content_id']),reverse=True)
        chosen=[]
        for distinct_pass in (True,False):
            for r in candidates:
                if len(chosen)>=target:break
                key=(kind,r['publisher'],r['period_end'],r['byte_size']//262144)
                if r['content_sha256'] in hashes or (distinct_pass and key in diversity):continue
                hashes.add(r['content_sha256']);diversity.add(key)
                chosen.append({**r,'verification_state':'verified','verification':verified[r['content_id']],
                    'priority':len(selected)+len(chosen)+1,'outcome_at_freeze':'DEFERRED'})
        selected.extend(chosen)
    if not selected:raise RuntimeError('No verified actionable candidates')
    manifest={'schema_version':1,'campaign_id':'document-backlog-recovery-20261026-pilot-100',
        'authorization':'User Phase 2 implementation/execution instruction: finite stored-document pilot; daily processing limits unchanged',
        'historical_authorization_record':'artifacts/acquisition-recovery-20261026/AUTHORIZATION.md (unchanged; consumed campaign excluded)',
        'frozen_at_utc':datetime.now(timezone.utc).isoformat(),'version':inventory['processing_version'],
        'network_mode':'none','external_requests':0,'maximum_documents':100,'daily_maximum':24,
        'per_batch_maximum':3,'reserve_new_arrival_operations':2,
        'inventory_sha256':inventory['contents_sha256'],'contents':selected,
        'reservation_policy':'Each attempted/cache-recovered content consumes one existing shared daily stage reservation; unused/interrupted reservations retained',
        'campaign_reservations_at_freeze':[]}
    save(manifest_path,manifest);(out/'PILOT_MANIFEST.sha256').write_text(digest(manifest)+'\n')
    print(json.dumps({'selected':len(selected),'types':{k:sum(r['artifact_type']==k for r in selected) for k in ('credit_rating','presentation','transcript')},'sha256':digest(manifest)}))


def run(root,out,limit,seconds,dry_run=False):
    from acquisition_lifecycle import lock
    from automatic_acquisition import verify_config,save,STATE
    from document_backlog_recovery import window_safe
    from phase4_documents import storage_preflight,STORE,IMAGE
    from phase4_schedule import prepare,reserve_daily_stage,cleanup_timed_out_container,settings
    from production_ownership import owner_gate
    from scrapper.operations.workers.local_document_campaign import digest
    from scrapper.sources.exchange.reporting_periods import IST
    if type(limit) is not int or not 1<=limit<=3:raise ValueError('Bounded batch limit is 1–3')
    if not 60<=seconds<=420:raise ValueError('Runtime bound must be 60–420 seconds')
    campaign=json.loads((out/'PILOT_AUTHORIZATION_AND_PLAN.json').read_text())
    checksum=(out/'PILOT_MANIFEST.sha256').read_text().strip()
    if digest(campaign)!=checksum:raise RuntimeError('Manifest drift')
    if dry_run:
        print(json.dumps({'status':'DRY_RUN','selected':len(campaign['contents']),
            'maximum_batch':limit,'reservations_consumed':0,'production_writes':0}));return
    if not remaining_contents(campaign,out,checksum):
        result={'status':'ACCOUNTED','selected':len(campaign['contents']),'reservations_consumed':0}
        print(json.dumps(result));return result
    # Every invocation, including a retry, needs current admission and capacity.
    with lock(root/'ops/activation/.scheduler-admission.lock'):
        with lock(root/'ops/activation/.nse-owner.lock'):
            window_safe(seconds+20);verify_config();owner=owner_gate()
            c=settings();capacity=storage_preflight()
            from scraper_storage_growth import account
            account(STORE,STATE/'scraper-storage-growth-v1.json',c,capacity,reserve_bytes=c['max_response_bytes']*2)
            now=datetime.now(timezone.utc);day=now.astimezone(IST).date().isoformat()
            used=0
            for folder in STATE.glob(day+'-*'):
                for name in ('daily-budget-reservation.json','execution-started.json'):
                    p=folder/name
                    if p.exists():
                        used+=json.loads(p.read_text()).get('stage')=='DOCUMENT_PROCESSING';break
            # Leave two shared operations for new arrivals; never expand 24/day.
            remaining=remaining_contents(campaign,out,checksum)
            count=min(limit,len(remaining),max(0,24-used-2))
            if count==0:
                save(out/'budget-deferred.json',{'at_utc':now,'used':used,'maximum':24,'reserved_for_new_arrivals':2})
                print(json.dumps({'status':'DEFERRED','reason':'daily budget','used':used}));return
            stamp=now.strftime('%Y%m%dT%H%M%S%fZ');batch=out/'batches'/stamp;batch.mkdir(parents=True)
            prepare(batch,'DOCUMENT_PROCESSING',authorized=True)
            plan=json.loads((batch/'plan.json').read_text())
            plan.update(campaign_sha256=checksum,campaign_limit=count,campaign_seconds=seconds)
            save(batch/'plan.json',plan)
            package=json.loads((batch/'manifest.json').read_text())
            package['plan_sha256']=hashlib.sha256((batch/'plan.json').read_bytes()).hexdigest()
            package['runtime_sha256']=hashlib.sha256((root/'ops/activation/local_document_campaign_runtime.py').read_bytes()).hexdigest()
            save(batch/'manifest.json',package);save(batch/'execution-storage-preflight.json',capacity)
            compose=json.loads((batch/'compose.json').read_text())
            service=compose['services']['document-once']
            service['entrypoint']=['python','/activation/local_document_campaign_runtime.py']
            # Stored-byte processing needs no publisher credential or session.
            service['volumes']=[m for m in service['volumes'] if m.get('target')!='/run/secrets/screener-session']
            service['environment'].pop('SCREENER_SESSION_FILE',None)
            service['volumes'].append({'type':'bind','source':str(out),'target':'/campaign','bind':{'create_host_path':False}})
            if service['network_mode']!='none' or service['cap_drop']!=['ALL']:raise RuntimeError('Isolation differs')
            save(batch/'compose.json',compose)
            reservations=[]
            for index in range(count):
                folder=STATE/(day+'-document-processing-phase2-'+stamp+'-'+str(index));folder.mkdir()
                if not reserve_daily_stage(folder,'DOCUMENT_PROCESSING',now):raise RuntimeError('Daily budget changed')
                save(folder/'campaign.json',{'campaign_sha256':checksum,'batch':str(batch),'slot':index})
                reservations.append(str(folder))
            save(batch/'reservations.json',reservations)
            # Same durable capacity publication as the routine, no identity change.
            save(STORE/'.trading_ai_document_store_capacity.json',capacity)
            save(batch/'execution-started.json',{'at_utc':now,'stage':'DOCUMENT_PROCESSING','owner':owner})
            args=['docker','compose','-f',str(batch/'compose.json'),'--profile','documents','run','--rm','--no-deps',
                '--name','trading-ai-phase4-routine','document-once']
            started=time.monotonic()
            try:
                with (batch/'runtime.log').open('w') as log:
                    result=subprocess.run(args,stdout=log,stderr=subprocess.STDOUT,timeout=seconds+30)
                status={'returncode':result.returncode,'total_seconds':time.monotonic()-started,
                    'reservations':len(reservations),'campaign_sha256':checksum}
            except subprocess.TimeoutExpired:
                cleanup_timed_out_container(batch)
                status={'returncode':75,'reason':'deadline; canonical committed extractions reusable','total_seconds':time.monotonic()-started}
            account(STORE,STATE/'scraper-storage-growth-v1.json',c,storage_preflight())
            save(batch/'outcome.json',status)
            owner_gate();print(json.dumps({'batch':str(batch),**status}))
            if status['returncode']:raise RuntimeError('Campaign batch stopped; inspect retained runtime log')
            return status
