"""One authorized finite follow-up; existing launchd owns all source/writer work.

This supervisor only installs the fixed approved config, observes and reports.
No new launchd job, wake action, source dispatch, migration, or extension.
"""
import argparse,fcntl,hashlib,json,os,shutil,ssl,subprocess,time
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from pathlib import Path
from automatic_acquisition import verify_config,LABEL
from phase4_documents import storage_preflight
from phase4_trial_preflight import snapshot_trial
from production_ownership import owner_gate
import phase4_trial_observer as observer
from phase4_schedule import jobs
from scrapper.documents.download.tls import verified_chain_context

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
OUT=ROOT/'artifacts/phase4/routine/followup-20261014'
PREPARED=ROOT/'artifacts/phase4/routine/gap-closure-20261013/followup-config.json'
START=datetime(2026,10,14,6,30,tzinfo=timezone.utc)
END=datetime(2026,10,14,12,30,tzinfo=timezone.utc)

def save(name,value):
    OUT.mkdir(parents=True,exist_ok=True)
    path=OUT/name;temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(value,indent=2,default=str)+'\n');temp.replace(path)

def validate_window(config,now,*,authorized_restart=False):
    if (datetime.fromisoformat(config['activated_at_utc'])!=START or
            datetime.fromisoformat(config['expires_at_utc'])!=END or
            config.get('verification_window_hours')!=6 or END-START!=timedelta(hours=6)):
        raise RuntimeError('Approved fixed six-hour scope changed')
    # Installation may start before noon, never after the first discovery slot.
    latest=END if authorized_restart else START+timedelta(minutes=4)
    if not START-timedelta(minutes=10)<=now<latest:
        raise RuntimeError('Activation outside fixed preflight admission interval; no replay or reschedule')

@contextmanager
def activation_locks(deadline):
    # Release admission if the owner is busy; never prevent its current work
    # from finishing. Only administrative activation retries, never job replay.
    with (HERE/'.scheduler-admission.lock').open('a') as admission,(HERE/'.nse-owner.lock').open('a') as owner:
        while True:
            acquired=False
            try:
                fcntl.flock(admission,fcntl.LOCK_EX|fcntl.LOCK_NB);acquired=True
                fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if acquired:fcntl.flock(admission,fcntl.LOCK_UN)
                if datetime.now(timezone.utc)>=deadline:raise
                save('activation-defer.json',{'at_utc':datetime.now(timezone.utc),'reason':'Scheduler admission or production owner busy','source_requests':0,'window_unchanged':True})
                time.sleep(1)
        try:yield
        finally:
            fcntl.flock(owner,fcntl.LOCK_UN);fcntl.flock(admission,fcntl.LOCK_UN)

def safety():
    manifest=verify_config()
    context=verified_chain_context()
    assert context.check_hostname and context.verify_mode==ssl.CERT_REQUIRED
    assert not context.verify_flags&ssl.VERIFY_X509_PARTIAL_CHAIN
    loaded=subprocess.check_output(['launchctl','print','gui/'+str(os.getuid())+'/'+LABEL],text=True,timeout=20)
    if str(HERE/'automatic_acquisition.py') not in loaded:raise RuntimeError('Existing LaunchAgent not loaded as expected')
    return {'manifest_verified':True,'tls_hostname_verified':True,'tls_required':True,'partial_chain':False,'launchagent_loaded':True}

def preservation():
    expected=json.loads((ROOT/'artifacts/phase4/routine/trial/preflight/preservation-hashes.json').read_text())
    for name,digest in expected.items():
        if hashlib.sha256((HERE/name).read_bytes()).hexdigest()!=digest:raise RuntimeError('Phase3 preservation drift: '+name)
    return expected

def prepare():
    if (OUT/'authorization.json').exists():raise RuntimeError('Already prepared; retain authorization')
    checks=safety();phase3=preservation()
    cfg=json.loads(PREPARED.read_text())
    assert cfg['enabled'] is False and cfg['historical_limit']==0 and cfg['concurrency']==1
    snapshot_trial(OUT/'authorization-preflight')
    original=ROOT/'artifacts/phase4/routine/trial'
    save('authorization.json',{'authorized_by':'User, current chat','start_utc':START,'end_utc':END,
        'scope':cfg,'source_requests':0,'no_automatic_extension':True,'checks':checks,
        'phase3_preservation':phase3,'supervisor_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'original_trial_hashes':{n:hashlib.sha256((original/n).read_bytes()).hexdigest() for n in ('REPORT.md','status.json')},
        'current_config':json.loads((HERE/'phase4_schedule.json').read_text())})
    save('status.json',{'phase':'AUTHORIZED_WAITING_FOR_START','start_utc':START,'end_utc':END,'new_document_proof':'NOT_RUN','production_window_activated':False})
    (OUT/'REPORT.md').write_text('# Phase 4 authorized follow-up — waiting for 14 October\n\nFixed window: 12:00–18:00 IST. Preliminary production safety checks passed. Fresh checks will run immediately before activation. No document work is authorized outside the window. Final outcomes will replace this report after expiry.\n')

def activate(*,authorized_restart=False):
    auth=json.loads((OUT/'authorization.json').read_text())
    assert hashlib.sha256(Path(__file__).read_bytes()).hexdigest()==auth['supervisor_sha256']
    cfg=dict(auth['scope']);validate_window(cfg,datetime.now(timezone.utc),authorized_restart=authorized_restart)
    admission_deadline=min(END,datetime.now(timezone.utc)+timedelta(minutes=6)) if authorized_restart else START+timedelta(minutes=3)
    if json.loads((HERE/'phase4_schedule.json').read_text())!=auth['current_config']:raise RuntimeError('Config changed since authorization; do not replace it')
    safety();preservation()
    # Fresh canonical snapshot before activation; read-only, no source requests.
    for attempt in range(36):
        attempt_path=OUT/f'activation-preflight-{attempt:02d}'
        try:
            snapshot_trial(attempt_path,runtime='phase4_followup_snapshot_runtime.py')
            shutil.copytree(attempt_path,OUT/'preflight')
            break
        except (BlockingIOError, RuntimeError) as error:
            # Administrative ownership contention only; never repair a failed
            # safety check or move the fixed window to recover lost time.
            busy=isinstance(error,BlockingIOError) or any(text in str(error) for text in
                ('active writable production mount', 'Host production ownership conflict', 'Unexpired production acquisition lease'))
            if not busy or datetime.now(timezone.utc)>=admission_deadline:raise
            save('activation-defer.json',{'at_utc':datetime.now(timezone.utc),'reason':str(error),'source_requests':0,'window_unchanged':True})
            time.sleep(10)
    else:raise RuntimeError('Ownership remained busy before first discovery slot; no reschedule')
    with activation_locks(admission_deadline):
        validate_window(cfg,datetime.now(timezone.utc),authorized_restart=authorized_restart)
        gate=owner_gate();manifest=verify_config();capacity=storage_preflight()
        if json.loads((HERE/'phase4_schedule.json').read_text())!=auth['current_config']:raise RuntimeError('Concurrent configuration change')
        for name in ('phase4_schedule.json','automatic-acquisition-manifest.json'):
            shutil.copyfile(HERE/name,OUT/('before-'+name))
        sealpath=ROOT/'artifacts/phase4/routine/rollout/seal.json'
        seal=json.loads(sealpath.read_text());shutil.copyfile(sealpath,OUT/'before-seal.json')
        cfg.update(enabled=True,initial_free_bytes=capacity['free_bytes'],authorization='User-authorized fixed six-hour follow-up: 14 October 2026 12:00–18:00 IST; no extension')
        save('activation-intent.json',{'at_utc':datetime.now(timezone.utc),'scope':cfg,'owner':gate,'capacity':capacity})
        path=HERE/'phase4_schedule.json';temp=path.with_suffix('.json.tmp')
        temp.write_text(json.dumps(cfg,indent=2)+'\n');temp.replace(path)
        digest=hashlib.sha256(path.read_bytes()).hexdigest();manifest['files']['ops/activation/phase4_schedule.json']=digest
        (HERE/'automatic-acquisition-manifest.json').write_text(json.dumps(manifest,indent=2)+'\n')
        seal['sources']['ops/activation/phase4_schedule.json']=digest
        seal['installed_dispatcher_manifest_sha256']=hashlib.sha256((HERE/'automatic-acquisition-manifest.json').read_bytes()).hexdigest()
        sealpath.write_text(json.dumps(seal,indent=2)+'\n')
        verify_config();preservation()
        assert jobs(END)==[]
        save('activation.json',{'status':'FIXED_WINDOW_INSTALLED','at_utc':datetime.now(timezone.utc),'scope':cfg,'safety':safety(),'phase3_unchanged':True,'source_requests':0,'production_db_writes':0})

def observed_rows():
    return [r for r in observer.read_receipts(START) if datetime.fromisoformat(r['admission']['started_at_utc'])<END]

def finalize(data):
    rows=observed_rows();failures=[];idle=[];latched=[];blocked=[]
    for row in rows:
        o=row['outcome'];name=Path(row['path']).name
        if 'stopped after a prior failure' in o.get('error',''):latched.append(name)
        elif o['status']=='STOP':failures.append({'slot':name,'outcome':o})
        elif o['status'] not in ('PASS','running_or_interrupted'):blocked.append({'slot':name,'outcome':o})
        for r in row['records']:
            if r.get('stage')=='document_stage_outcome' and r['result'].get('status')=='idle':idle.append(name)
    opportunities=[{'stage':'DOCUMENT_DISCOVERY','at_ist':'2026-10-14 12:05'}]
    opportunities += [{'stage':stage,'at_ist':f'2026-10-14 {hour:02d}:{minute}'} for hour in range(12,18) for stage,minute in (('DOCUMENT_DOWNLOAD','25'),('DOCUMENT_PROCESSING','35'))]
    actual={r['admission']['scheduled_at_ist'][:16] for r in rows}
    snap=data.get('final_database_snapshot',{})
    result={'opportunities':opportunities,'actual_executions':len(rows),
        'missing_opportunities':[o for o in opportunities if o['at_ist'] not in actual],
        'genuine_stage_failures':failures,'downstream_latch_stops':latched,'idle_slots':idle,'other_outcomes':blocked,
        'new_artifact_count':len(snap.get('new_document_lifecycles',[])),
        'new_document_lifecycles':snap.get('new_document_lifecycles',[]),
        'strict_unattended_verifier':data['new_document_proof'],
        'scope_attribution':'Whole-database new rows can include Phase3; correlate stage-selected IDs and timestamps.',
        'source_requests_by_supervisor':0,'production_db_writes_by_supervisor':0,'automatic_extension':False}
    data['followup_outcome']=result;save('status.json',data)
    original=ROOT/'artifacts/phase4/routine/trial';auth=json.loads((OUT/'authorization.json').read_text())
    assert all(hashlib.sha256((original/n).read_bytes()).hexdigest()==h for n,h in auth['original_trial_hashes'].items())
    text=(OUT/'REPORT.md').read_text()
    text+='\n## Fixed six-hour follow-up outcome\n\n'
    text+=f"Scheduled opportunities: 13 (one discovery, six downloads, six processing). Actual launchd receipts: {len(rows)}. Missing opportunities: {json.dumps(result['missing_opportunities'])}. No patrol or daily rating slot falls inside this window.\n\n"
    text+=f"New canonical artifacts: {result['new_artifact_count']}. Genuine stage failures: {json.dumps(failures)}. Idle slots: {json.dumps(idle)}. Downstream existing-latch refusals: {json.dumps(latched)}. Other admission outcomes: {json.dumps(blocked)}.\n\n"
    downloaded={a['artifact_id'] for a in snap.get('new_download_attempts',[]) if a['result_status']=='validated'}
    extracted={e['content_id'] for e in snap.get('new_extractions',[]) if e['extraction_status']=='ok'}
    text+=f"Interval validated download artifact IDs: {sorted(downloaded)}; successful extraction content IDs: {sorted(extracted)}. These whole-database rows require receipt correlation before unattended attribution.\n\n"
    text+=f"Strict unattended verifier: **{data['new_document_proof']['status']}**.\n\n"
    if data['new_document_proof']['status']!='PASS':
        text+='Phase 4 is not complete. Missing evidence: one newly eligible document with source-evidenced company/period and launchd discovery → validated download → verified immutable archive → successful extraction/work completion. Idle/no suitable arrival is not a software failure. Genuine failures, if any, are listed separately. No repeat window or extension is activated.\n\n'
    else:text+='Qualifying lineage and exact receipts are recorded in status.json; only those verified lifecycles establish completion.\n\n'
    text+='Full source URLs, company identities, reporting periods/events, queue attempts, stored-file checksums, inbox/work and extraction results are in status.json → followup_outcome → new_document_lifecycles. Original trial evidence and Phase3 scheduling remain preserved.\n'
    (OUT/'REPORT.md').write_text(text)
    save('supervisor-finished.json',{'finished_at_utc':datetime.now(timezone.utc),'verifier':data['new_document_proof']['status'],'report':str(OUT/'REPORT.md'),'no_extension':True})

def run(*,authorized_restart=False):
    # A singleton read-only supervisor, not a production acquisition worker.
    with (OUT/'.supervisor.lock').open('a') as singleton:
        fcntl.flock(singleton,fcntl.LOCK_EX|fcntl.LOCK_NB)
        save('supervisor-started.json',{'pid':os.getpid(),'at_utc':datetime.now(timezone.utc),'start_utc':START,'end_utc':END})
        while datetime.now(timezone.utc)<START-timedelta(minutes=2):time.sleep(min(60,(START-timedelta(minutes=2)-datetime.now(timezone.utc)).total_seconds()))
        activate(authorized_restart=authorized_restart)
        save('status.json',{'phase':'FIXED_WINDOW_INSTALLED_WAITING_FOR_START','start_utc':START,'end_utc':END,'new_document_proof':'NOT_RUN'})
        while datetime.now(timezone.utc)<START:time.sleep(min(60,(START-datetime.now(timezone.utc)).total_seconds()))
        observer.OUT=OUT
        observer.snapshot_trial=lambda out,final=False:snapshot_trial(out,final=final,runtime='phase4_followup_snapshot_runtime.py')
        observer.main()
        finalize(json.loads((OUT/'status.json').read_text()))

def prepare_authorized_restart():
    global OUT
    parent=OUT
    if json.loads((parent/'status.json').read_text())['phase']!='SUPERVISOR_STOPPED':
        raise RuntimeError('Restart requires a stopped, unactivated supervisor')
    if (parent/'activation.json').exists() or (parent/'activation-intent.json').exists():
        raise RuntimeError('Prior activation must be reconciled before restart')
    auth=json.loads((parent/'authorization.json').read_text())
    validate_window(auth['scope'],datetime.now(timezone.utc),authorized_restart=True)
    OUT=parent/('restart-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    auth.update(authorized_by='User explicitly instructed: well start it; remaining original window only',
        supervisor_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        prior_authorization=str(parent/'authorization.json'),no_missed_slot_replay=True)
    save('authorization.json',auth)
    save('status.json',{'phase':'AUTHORIZED_RESTART_PREFLIGHT','start_utc':START,'end_utc':END,'no_extension':True})
    save('restart-context.json',{'prior_status':json.loads((parent/'status.json').read_text()),'original_window_retained':True,'missed_slots_replayed':False})
    (parent/'restart-latest.json').write_text(json.dumps({'path':str(OUT),'at_utc':str(datetime.now(timezone.utc))},indent=2)+'\n')

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--prepare',action='store_true');p.add_argument('--run',action='store_true');p.add_argument('--restart',action='store_true');a=p.parse_args()
    try:
        if a.prepare:prepare()
        elif a.run:run()
        elif a.restart:prepare_authorized_restart();run(authorized_restart=True)
        else:p.error('Choose prepare or finite run')
    except Exception as e:
        if (OUT/'status.json').exists():shutil.copyfile(OUT/'status.json',OUT/'last-observation-status.json')
        save('status.json',{'phase':'SUPERVISOR_STOPPED','at_utc':datetime.now(timezone.utc),'error':str(e),'strict_unattended_verifier':'NOT_ESTABLISHED','no_extension':True})
        save('supervisor-error.json',{'at_utc':datetime.now(timezone.utc),'error':str(e),'no_automatic_retry':True,'no_extension':True})
        (OUT/'REPORT.md').write_text('# Phase 4 follow-up — supervisor stopped\n\n'+str(e)+'\n\nNo automatic extension, rescheduling or repair. Retained receipts remain authoritative.\n')
        raise
