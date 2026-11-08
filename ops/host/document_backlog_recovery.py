"""Finite Phase 2 host operations, serialized with the installed dispatcher."""
import argparse
from datetime import datetime, timedelta
import fcntl
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'ops/activation'),str(ROOT/'ops/host'),str(ROOT)]
from automatic_acquisition import SITE_PACKAGES
sys.path.insert(0,SITE_PACKAGES)
OUT=ROOT/'artifacts/document-backlog-recovery-20261026'


def window_safe(seconds):
    from scrapper.sources.exchange.reporting_periods import IST
    now=datetime.now(IST)
    # Finish before ANY scheduled acquisition stage, including financial slots.
    from automatic_acquisition import jobs
    for _,_,trigger in jobs():
        due=trigger.get_next_fire_time(None,now-timedelta(seconds=60))
        if due and -60 <= (due-now).total_seconds() <= seconds+30:
            raise BlockingIOError('Reserved scheduler window: '+str(due))


def audit(action, limit, seconds):
    from automatic_acquisition import verify_config, save
    from phase4_documents import IMAGE,STORE,storage_preflight
    from production_ownership import owner_gate
    from acquisition_lifecycle import lock, VOLUME, run
    OUT.mkdir(exist_ok=True);raw=OUT/'raw';raw.mkdir(exist_ok=True)
    metadata_only=action=='metadata'
    refresh=action in ('refresh','metadata')
    if refresh:action='census'
    with lock(ROOT/'ops/activation/.scheduler-admission.lock'):
        with lock(ROOT/'ops/activation/.nse-owner.lock'):
            window_safe(seconds+15)
            verify_config();owner=owner_gate()
            try:storage=storage_preflight()
            except RuntimeError as exc:
                if not metadata_only or 'External document volume is not mounted' not in str(exc):raise
                storage={'status':'UNAVAILABLE','reason':str(exc),'physical_bytes_read':0,
                    'observed_at_utc':datetime.now().astimezone().isoformat()}
            if refresh:
                raw=raw/('refresh-'+datetime.now().strftime('%Y%m%dT%H%M%S'));raw.mkdir()
            volume=json.loads(run(['docker','volume','inspect',VOLUME]).stdout)[0]
            if volume['Labels']['com.docker.compose.project']!='trading-ai':raise RuntimeError('Wrong volume')
            save(raw/'preflight.json',{'owner':owner,'storage':storage,'volume':volume,
                'at_utc':datetime.now().astimezone().isoformat()})
            if action in ('verify','sample','candidates'):
                from document_backlog_audit import verify_batch
                stamp=datetime.now().strftime('%Y%m%dT%H%M%S')
                result=verify_batch(raw,limit,seconds,action=='sample',archive_root=STORE,candidates=action=='candidates')
                save(raw/(action+'-'+stamp+'.json'),result)
                return
            args=['docker','run','--rm','--pull=never','--network=none','--read-only',
                '--cap-drop=ALL','--security-opt=no-new-privileges','--cpus=.5','--memory=1536m',
                '--tmpfs','/tmp:rw,noexec,nosuid,size=32m',
                '--mount','type=volume,source='+VOLUME+',target=/data,readonly',
                '--mount','type=bind,source='+str(ROOT/'ops/activation')+',target=/activation,readonly',
                '--mount','type=bind,source='+str(ROOT/'ops/host')+',target=/host,readonly',
                '--mount','type=bind,source='+str(ROOT/'scrapper')+',target=/app/scrapper,readonly',
                '--mount','type=bind,source='+str(STORE)+',target=/documents,readonly',
                '--mount','type=bind,source='+str(raw)+',target=/evidence',
                '--mount','type=bind,source='+str(OUT)+',target=/campaign,readonly',
                '--env','PYTHONDONTWRITEBYTECODE=1','--entrypoint','python',IMAGE,
                '/host/document_backlog_audit.py',action,'--limit',str(limit),'--seconds',str(seconds)]
            if metadata_only:
                # Explicit database-only inspection: no mount, fallback folder,
                # physical-byte claim or storage-readiness exemption for work.
                mount='type=bind,source='+str(STORE)+',target=/documents,readonly'
                index=args.index(mount);del args[index-1:index+1]
            start=time.monotonic()
            r=subprocess.run(args,capture_output=True,text=True,timeout=180)
            stamp=datetime.now().strftime('%Y%m%dT%H%M%S')
            save(raw/(action+'-'+stamp+'.json'),{'args':args,'returncode':r.returncode,
                'stdout':r.stdout,'stderr':r.stderr,'seconds':time.monotonic()-start})
            print(r.stdout or r.stderr)
            if r.returncode:raise RuntimeError('Audit failed; retained exact error')
            owner_gate()


def finish():
    """Finish read-only evidence in one authorization, never another writer."""
    from automatic_acquisition import save
    import acquisition_lifecycle as life
    from acquisition_observations import redact
    batches=0;deferrals=0
    raw=OUT/'raw'
    expected=sum(1 for _ in (raw/'contents.jsonl').open())
    while batches<120 and deferrals<360:
        checked={json.loads(line)['content_id'] for line in (raw/'verification.jsonl').open()}
        if len(checked)>=expected:break
        try:audit('verify',2000,40)
        except (BlockingIOError,RuntimeError) as exc:
            if not any(s in str(exc) for s in ('Reserved scheduler window','Active owner has not drained')):raise
            deferrals+=1;print(json.dumps({'deferred':str(exc)}),flush=True);time.sleep(10);continue
        batches+=1;time.sleep(5)
    if len({json.loads(line)['content_id'] for line in (raw/'verification.jsonl').open()})<expected:
        raise RuntimeError('Bounded verification stopped incomplete; retain checkpoint and resume finish')
    for _ in range(450):
        try:audit('refresh',100,40);break
        except (BlockingIOError,RuntimeError) as exc:
            if not any(s in str(exc) for s in ('Reserved scheduler window','Active owner has not drained')):raise
            print(json.dumps({'deferred':str(exc)}),flush=True);time.sleep(2)
    else:raise RuntimeError('Final read-only snapshot remained busy')
    result=redact(life.status(include_summary=True));save(raw/'live-status.json',result)
    import document_backlog_report
    document_backlog_report.main()
    save(raw/'finish.json',{'at_utc':datetime.now().astimezone().isoformat(),
        'verification_batches':batches,'deferrals':deferrals,'status':'EVIDENCE_REBUILT'})


def closeout():
    """Independent database evidence when physical verification is blocked."""
    from automatic_acquisition import save
    from acquisition_observations import redact
    import acquisition_lifecycle as life
    for _ in range(450):
        try:audit('metadata',100,40);break
        except (BlockingIOError,RuntimeError) as exc:
            if not any(s in str(exc) for s in ('Reserved scheduler window','Active owner has not drained')):raise
            print(json.dumps({'deferred':str(exc)}),flush=True);time.sleep(2)
    else:raise RuntimeError('Independent database snapshot remained busy')
    result=redact(life.status(include_summary=True));save(OUT/'raw/live-status.json',result)
    snapshots=list((OUT/'raw').glob('refresh-*/inventory.json'))
    latest=max(snapshots,key=lambda p:json.loads(p.read_text())['observed_at_utc'])
    storage=json.loads((latest.parent/'preflight.json').read_text())['storage']
    save(OUT/'raw/physical-verification-blocked.json',{'status':'BLOCKED_STORAGE_UNMOUNTED' if storage.get('status')=='UNAVAILABLE' else 'INCOMPLETE_PHYSICAL_VERIFICATION',
        'observed_at_utc':datetime.now().astimezone().isoformat(),
        'checked_content_ids':len({json.loads(line)['content_id'] for line in (OUT/'raw/verification.jsonl').open()}),
        'processing_reservations_consumed':0,'publisher_requests':0,
        'storage_observation':storage,
        'message':'Read-only database-only closeout; incomplete physical verification is not success'})
    import document_backlog_report
    document_backlog_report.main()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('census','refresh','metadata','sample','verify','verify-all','finish','closeout','stop-verification','status','select','process','process-available','dry-run','candidates','wake','discovery-canary','download-canary','rating-canary'))
    p.add_argument('--limit',type=int,default=100)
    p.add_argument('--seconds',type=int,default=40)
    p.add_argument('--wait',action='store_true',help='Retry only admission/window deferrals, with locks released')
    a=p.parse_args()
    if a.action=='finish':finish();return
    if a.action=='closeout':closeout();return
    if a.action=='stop-verification':
        import os,signal
        listing=subprocess.check_output(['ps','-axo','pid,command'],text=True)
        stopped=[]
        for line in listing.splitlines():
            fields=line.strip().split(None,1)
            if len(fields)==2 and 'python -B ops/host/document_backlog_recovery.py verify-all ' in fields[1]:
                pid=int(fields[0]);os.kill(pid,signal.SIGINT);stopped.append(pid)
        print(json.dumps({'interrupted_own_readonly_verifiers':stopped}));return
    if a.action=='process-available':
        from local_document_campaign import run as run_campaign
        batches=0
        for _ in range(240):
            try:
                result=run_campaign(ROOT,OUT,a.limit,a.seconds)
                if result is None or result.get('status')=='ACCOUNTED':break
                batches+=1
                if batches>=8:break
                time.sleep(2)
            except (BlockingIOError,RuntimeError) as exc:
                if not any(s in str(exc) for s in ('Reserved scheduler window','Active owner has not drained')):raise
                print(json.dumps({'deferred':str(exc)}),flush=True);time.sleep(2)
        return
    if a.wait:
        for _ in range(120):
            try:
                if a.action=='process':
                    from local_document_campaign import run as run_campaign
                    run_campaign(ROOT,OUT,a.limit,a.seconds)
                elif a.action in ('candidates','census','refresh'):audit(a.action,a.limit,a.seconds)
                elif a.action=='wake':
                    import acquisition_lifecycle as life
                    from automatic_acquisition import save
                    result=life.wake();save(OUT/'raw/wake.json',result);print(json.dumps(result,default=str))
                elif a.action in ('discovery-canary','download-canary','rating-canary'):
                    from acquisition_lifecycle import lock
                    import automatic_acquisition as scheduler
                    from scrapper.sources.exchange.reporting_periods import IST
                    kind=a.action.split('-')[0]
                    with lock(ROOT/'ops/activation/.scheduler-admission.lock'):
                        window_safe(120);scheduler.verify_config()
                        result=scheduler.run_slot('document-'+kind,'DOCUMENT_'+kind.upper(),datetime.now(IST))
                        scheduler.save(OUT/'raw'/(a.action+'.json'),result)
                        print(json.dumps(result,default=str))
                else:raise ValueError('Wait is limited to census, candidates or processing')
                return
            except (BlockingIOError,RuntimeError) as exc:
                if not any(s in str(exc) for s in ('Reserved scheduler window','Active owner has not drained')):raise
                print(json.dumps({'deferred':str(exc)}),flush=True);time.sleep(2)
        raise RuntimeError('Admission remained busy; no work attempted')
    if a.action in ('select','process','dry-run'):
        import local_document_campaign as campaign
        if a.action=='select':campaign.freeze(ROOT,OUT)
        else:campaign.run(ROOT,OUT,a.limit,a.seconds,a.action=='dry-run')
    elif a.action=='status':
        import acquisition_lifecycle as life
        from acquisition_observations import redact
        OUT.mkdir(exist_ok=True);(OUT/'raw').mkdir(exist_ok=True)
        result=redact(life.status(include_summary=True))
        from automatic_acquisition import save
        save(OUT/'raw/live-status.json',result)
        held=subprocess.run(['lsof','-nP',str(ROOT/'ops/activation/.scheduler-admission.lock')],capture_output=True,text=True)
        save(OUT/'raw/lock-owner.txt.json',{'stdout':held.stdout,'stderr':held.stderr})
        print(json.dumps({k:result.get(k) for k in ('observed_at_utc','status','currently_executing_stage','inspection_errors')}))
        print(held.stdout)
    elif a.action=='verify-all':
        # Release both locks between bounded batches. Sleeping never owns a
        # lock; scheduled acquisitions always receive their reserved window.
        completed_batches=0;deferrals=0
        while completed_batches<120 and deferrals<360:
            try:audit('verify',a.limit,a.seconds)
            except (BlockingIOError,RuntimeError) as exc:
                if not any(s in str(exc) for s in ('Reserved scheduler window','Active owner has not drained')):raise
                deferrals+=1
                print(json.dumps({'deferred':str(exc)}),flush=True)
                time.sleep(10);continue
            completed_batches+=1
            raw=OUT/'raw'
            if sum(1 for _ in (raw/'verification.jsonl').open())>=sum(1 for _ in (raw/'contents.jsonl').open()):break
            time.sleep(2)
    else:audit(a.action,a.limit,a.seconds)


if __name__=='__main__':main()
