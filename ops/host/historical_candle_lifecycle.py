"""Host controls for the already authorized candle campaign; no recurring schedule."""
import fcntl
import json
import os
from pathlib import Path
import shlex
import signal
import sqlite3
import subprocess
import time

from experiments.historical_candle_archive_v1 import control
from experiments.historical_candle_archive_v1.storage import ROOT,OUT,discover,verify,dump,sha

STATE=OUT/'hcd02'
RUNTIME=STATE/'candle-runtime.json'
MODULE='experiments.historical_candle_archive_v1.rollout'
ROLES={'acquisition':'authorized-full','reporter':'watch-report'}


def read(path):
    return json.loads(path.read_text())


def busy(path):
    if not path.exists():return False
    with path.open('rb') as f:
        try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return True
        fcntl.flock(f,fcntl.LOCK_UN);return False


def process(pid):
    if not isinstance(pid,int) or pid<=1:return None
    result=subprocess.run(['/bin/ps','-ww','-p',str(pid),'-o','stat=','-o','lstart=','-o','command='],capture_output=True,text=True,timeout=5)
    if result.returncode:return None
    text=result.stdout.strip();parts=text.split(None,6)
    if len(parts)!=7 or 'Z' in parts[0]:return None
    return dict(start=' '.join(parts[1:6]),command=parts[6])


def matches(command,action):
    if MODULE not in command:return False
    try:args=shlex.split(command)
    except ValueError:return False
    return any(args[i:i+3]==['-m',MODULE,action] for i in range(len(args)-2))


def registered(role):
    path=STATE/f'{role}-worker.json'
    if not path.exists():return None
    record=read(path)
    if record.get('state')!='RUNNING':return None
    current=process(record['pid'])
    if current and current['start']==record['process_started'] and matches(current['command'],ROLES[role]):
        return record
    return None


def legacy():
    campaign=STATE/'full-campaign.json'
    if not campaign.exists():return []
    result=subprocess.run(['/bin/ps','-axww','-o','pid=','-o','command='],capture_output=True,text=True,timeout=10,check=True)
    known=set()
    for role in ROLES:
        path=STATE/f'{role}-worker.json'
        if not path.exists():continue
        record=read(path);current=process(record['pid'])
        if current and current['start']==record['process_started'] and matches(current['command'],ROLES[role]):
            known.add(record['pid'])  # Includes harmless interpreter teardown after EXITED.
    found=[]
    for line in result.stdout.splitlines():
        item=line.strip().split(None,1)
        if len(item)!=2:continue
        pid=int(item[0]);command=item[1]
        if pid in known:continue
        role=next((role for role,action in ROLES.items() if matches(command,action)),None)
        if not role:continue
        cwd=subprocess.run(['/usr/sbin/lsof','-a','-p',str(pid),'-d','cwd','-Fn'],capture_output=True,text=True,timeout=10)
        if 'n'+str(ROOT) not in cwd.stdout.splitlines():continue
        current=process(pid)
        if current:found.append(dict(pid=pid,role=role,process_started=current['start']))
    return found


def status():
    if not (STATE/'owner-authorization.json').exists():return dict(status='NOT_CONFIGURED')
    roles={role:registered(role) for role in ROLES}
    return dict(status='PAUSED' if control.PAUSE.exists() else 'RUNNING' if any(roles.values()) else 'IDLE',
        paused=control.PAUSE.exists(),workers=roles,unmanaged_workers=legacy(),
        campaign=read(STATE/'full-campaign.json') if (STATE/'full-campaign.json').exists() else None)


def pause(wait=660):
    if not (STATE/'owner-authorization.json').exists():return dict(status='NOT_CONFIGURED')
    if not control.PAUSE.exists():dump(control.PAUSE,dict(reason='SCRAPER_SHUTDOWN',owner_authorization_preserved=True))
    deadline=time.monotonic()+wait
    while True:
        unowned=legacy()
        if unowned:raise RuntimeError('Unmanaged historical candle process requires reviewed handoff; shutdown is not safe')
        running=any(registered(role) or busy(STATE/f'.{role}-owner.lock') for role in ROLES)
        locked=busy(STATE/'.full-campaign.lock')
        if not running and not locked:break
        if time.monotonic()>=deadline:raise RuntimeError('Historical candle workers have not drained; keep Passport connected')
        time.sleep(.2)
    return dict(status='PAUSED',workers_drained=True,authorization_preserved=True)


def wake():
    if not (STATE/'owner-authorization.json').exists():return dict(status='NOT_CONFIGURED')
    if control.SHARED_PAUSE.exists():raise RuntimeError('Shared acquisition admissions are paused')
    with (STATE/'.lifecycle-operator.lock').open('a+b') as owner:
        fcntl.flock(owner,fcntl.LOCK_EX)
        unowned=legacy()
        if unowned:raise RuntimeError('Unmanaged historical candle owner exists; refusing duplicate start')
        authorization=read(STATE/'owner-authorization.json')
        for name,key in [('full-universe-manifest.json','native_manifest_sha256'),('hcd02/special-full-manifest.json','session_repair_manifest_sha256')]:
            if sha((OUT/name).read_bytes())!=authorization[key]:raise RuntimeError('Authorized candle manifest changed')
        campaign=read(STATE/'full-campaign.json') if (STATE/'full-campaign.json').exists() else {}
        if campaign.get('phase')=='FINISHED' and (STATE/'owner-repair-report-ready.json').exists():
            return dict(status='COMPLETE',started=[])
        mount,info=discover();verify(mount,mount.stat().st_dev)
        runtime=read(RUNTIME)
        python=Path(runtime['python']);dependencies=Path(runtime['dependencies'])
        if not python.is_file() or not dependencies.is_dir():raise RuntimeError('Pinned candle runtime is missing')
        env=os.environ.copy();env['PYTHONPATH']=str(dependencies)+os.pathsep+str(ROOT);env['PYTHONDONTWRITEBYTECODE']='1'
        subprocess.run([str(python),'-B','-c','import pyarrow'],env=env,cwd=ROOT,check=True,capture_output=True,timeout=30)
        control.PAUSE.unlink(missing_ok=True)
        fd=os.open(STATE,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)
        started=[]
        try:
            for role,action in ROLES.items():
                if registered(role):continue
                if busy(STATE/f'.{role}-owner.lock'):raise RuntimeError(f'Candle {role} owner is busy but its identity cannot be established')
                with (STATE/f'{role}-runtime.log').open('ab') as log:
                    child=subprocess.Popen([str(python),'-B','-m',MODULE,action],cwd=ROOT,env=env,
                        stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
                deadline=time.monotonic()+30
                while not registered(role):
                    if child.poll() is not None:raise RuntimeError(f'Candle {role} exited during startup; see its runtime log')
                    if time.monotonic()>=deadline:raise RuntimeError(f'Candle {role} registration timed out')
                    time.sleep(.1)
                started.append(dict(role=role,pid=child.pid))
        except BaseException:
            dump(control.PAUSE,dict(reason='CANDLE_WAKE_FAILED',authorization_preserved=True))
            pause(wait=60)
            raise
        return dict(status='RUNNING',started=started,volume_uuid=info['VolumeUUID'],resume='CHECKSUM_VERIFIED_MANIFEST; NO_FULL_HISTORY_REFETCH')


def handoff_legacy(wait=660):
    """One installation handoff: interrupt only at a frozen, committed boundary.

    Ordinary shutdown never signals a writer. This migration is needed only for
    the campaign launched before cooperative request-boundary controls existed.
    """
    dump(control.PAUSE,dict(reason='INSTALL_LIFECYCLE_HANDOFF',authorization_preserved=True))
    old=legacy();deadline=time.monotonic()+wait
    for item in sorted(old,key=lambda r:r['role']=='acquisition'):
        pid=item['pid']
        if item['role']=='acquisition':
            mount,_=discover();verify(mount,mount.stat().st_dev)
            database=mount/'passport/trading-ai/market-data/historical-candle-archive-v1/inventory.sqlite3'
            while True:
                current=process(pid)
                if not current:break
                if current['start']!=item['process_started']:raise RuntimeError('Candle process identity changed')
                stopped=False;safe=False
                try:
                    os.kill(pid,signal.SIGSTOP);stopped=True
                    db=sqlite3.connect(f'file:{database}?mode=rw',uri=True,timeout=0)
                    try:
                        db.execute('BEGIN IMMEDIATE')
                        safe=db.execute("SELECT COUNT(*) FROM requests WHERE status IN ('IN_FLIGHT','RAW_SAVED')").fetchone()[0]==0
                        db.rollback()
                    finally:db.close()
                    if safe:os.kill(pid,signal.SIGINT)
                except sqlite3.OperationalError:
                    pass  # Busy means a write is in progress: resume, never interrupt it.
                finally:
                    if stopped:os.kill(pid,signal.SIGCONT)
                if safe:break
                if time.monotonic()>=deadline:raise RuntimeError('No committed candle handoff boundary found; job left running')
                time.sleep(.1)
        else:
            current=process(pid)
            if current and current['start']==item['process_started']:os.kill(pid,signal.SIGINT)
        while process(pid):
            if time.monotonic()>=deadline:raise RuntimeError('Legacy candle process did not exit; keep Passport attached')
            time.sleep(.2)
    result=pause(wait=max(0,deadline-time.monotonic()))
    for filename in ('full-campaign.json','full-execution.json'):
        path=STATE/filename
        if path.exists():
            record=read(path)
            if record.get('status')=='STOPPED' and record.get('reason')=='KeyboardInterrupt':
                record.update(status='PAUSED',reason='OWNER_CONTROLLED_LIFECYCLE_HANDOFF',previous_handoff_reason='KeyboardInterrupt')
                dump(path,record)
    dump(STATE/'lifecycle-handoff.json',dict(status='PASS',previous_workers=old,checkpoint_boundary='No in-flight/raw-saved requests; SQLite write lock acquired',result=result))
    return result


def install_handoff():
    mount,_=discover();verify(mount,mount.stat().st_dev)
    database=mount/'passport/trading-ai/market-data/historical-candle-archive-v1/inventory.sqlite3'
    db=sqlite3.connect(f'file:{database}?mode=ro',uri=True,timeout=30)
    previous={r[0]:tuple(r[1:]) for r in db.execute("SELECT request_id,raw_sha256,received_at,attempts,row_count FROM requests WHERE status='SUCCESS'").fetchall()};db.close()
    drained=handoff_legacy()
    db=sqlite3.connect(f'file:{database}?mode=ro',uri=True,timeout=30)
    current={r[0]:tuple(r[1:]) for r in db.execute("SELECT request_id,raw_sha256,received_at,attempts,row_count FROM requests WHERE status='SUCCESS'").fetchall()}
    integrity=db.execute('PRAGMA quick_check').fetchone()[0]
    pending=db.execute("SELECT COUNT(*) FROM requests WHERE status IN ('IN_FLIGHT','RAW_SAVED')").fetchone()[0];db.close()
    if integrity!='ok' or pending or any(current.get(k)!=v for k,v in previous.items()):
        raise RuntimeError('Candle lifecycle handoff verification failed; left paused')
    resumed=wake() if not control.SHARED_PAUSE.exists() else dict(status='PAUSED_BY_SHARED_LIFECYCLE')
    result=dict(status='PASS',previous_successful_requests_preserved=len(previous),successful_requests_after_drain=len(current),
        sqlite_integrity=integrity,in_flight_after_drain=pending,drained=drained,resumed=resumed)
    dump(STATE/'lifecycle-installation-verification.json',result)
    return result


def verify_pause_resume():
    mount,_=discover();verify(mount,mount.stat().st_dev)
    root=mount/'passport/trading-ai/market-data/historical-candle-archive-v1'
    def snapshot():
        db=sqlite3.connect(f'file:{root}/inventory.sqlite3?mode=ro',uri=True,timeout=30)
        try:
            rows={r[0]:tuple(r[1:]) for r in db.execute("SELECT request_id,raw_sha256,received_at,attempts,row_count FROM requests WHERE status='SUCCESS'").fetchall()}
            pending=db.execute("SELECT COUNT(*) FROM requests WHERE status IN ('IN_FLIGHT','RAW_SAVED')").fetchone()[0]
            return rows,pending
        finally:db.close()
    before,_=snapshot();paused=pause();after,pending=snapshot()
    if pending or busy(root/'.writer.lock') or any(after.get(k)!=v for k,v in before.items()):
        raise RuntimeError('Candle cooperative shutdown verification failed; left paused')
    resumed=wake()
    result=dict(status='PASS',shutdown=paused,no_active_archive_writer=True,in_flight_after_pause=pending,
                previously_saved_requests_unchanged=len(before),completed_after_drain=len(after),wake=resumed)
    dump(STATE/'cooperative-pause-resume-verification.json',result)
    return result


def seal_lifecycle():
    """Reconcile only this authorized operator integration, under existing gates."""
    import sys
    sys.path.insert(0,str(ROOT/'ops/host'))
    import acquisition_lifecycle as lifecycle
    import automatic_acquisition as dispatcher
    manifest=lifecycle.ACTIVATION/'automatic-acquisition-manifest.json'
    with lifecycle.lock(lifecycle.STATE/'operator.lock',create=True):
        with lifecycle.lock(lifecycle.ACTIVATION/'.scheduler-admission.lock',wait=660):
            with lifecycle.lock(lifecycle.ACTIVATION/'.nse-owner.lock',wait=660):
                payload=read(manifest)
                drift=[name for name,digest in payload['files'].items() if sha((ROOT/name).read_bytes())!=digest]
                allowed={'ops/host/acquisition_lifecycle.py'}
                if set(drift)-allowed:raise RuntimeError('Unrelated dispatcher drift; refused to reseal: '+str(drift))
                if lifecycle.PAUSE.exists():raise RuntimeError('Shared lifecycle is paused; refusing to change its stored configuration identity')
                backup=STATE/'lifecycle-dispatcher-manifest-before.json'
                if not backup.exists():dump(backup,payload)
                before=sha(manifest.read_bytes());added=[]
                paths=[ROOT/'ops/host/acquisition_lifecycle.py',ROOT/'ops/host/historical_candle_lifecycle.py',RUNTIME]
                paths += [p for p in (ROOT/'experiments/historical_candle_archive_v1').glob('*.py') if not p.name.startswith('test_')]
                for path in paths:
                    name=str(path.relative_to(ROOT))
                    if name not in payload['files']:added.append(name)
                    payload['files'][name]=sha(path.read_bytes())
                lifecycle.atomic(manifest,payload)
                dispatcher.verify_config()
                result=dict(status='PASS',changed_existing=drift,added=added,before_manifest_sha256=before,
                    after_manifest_sha256=sha(manifest.read_bytes()),authorization='Owner request to integrate candle backfill with ./scraper wake/shutdown',
                    dispatch_schedules_changed=False,document_intelligence_logic_changed=False)
                dump(STATE/'lifecycle-dispatcher-seal.json',result)
                return result
