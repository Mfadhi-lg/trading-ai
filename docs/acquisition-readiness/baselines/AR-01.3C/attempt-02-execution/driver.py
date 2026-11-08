"""Exact two-write canary once; all remaining actions RO/copies/evidence only."""
import fcntl,hashlib,importlib.util,json,os,subprocess,sys,time
from collections import Counter
from datetime import datetime,timedelta
from pathlib import Path

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[4]
KEY='AR-01.3C/control-ownership/2026-10-08/v1'
BASEHASH='d773f4863e41266ca2cbbd76f1d542eb888878be1380e40114c34b4164fe4afc'
spec=importlib.util.spec_from_file_location('host',ROOT/'ops/docker/canary/ar013_readonly_host.py');host=importlib.util.module_from_spec(spec);spec.loader.exec_module(host)
POST=ROOT/'artifacts/acquisition-readiness/AR-01.3C/attempt-02-post-canary'
PREVOL='trading-ai-ar013c-attempt02-baseline-fingerprint-20261008'
POSTVOL='trading-ai-ar013c-attempt02-post-fingerprint-20261008'


def save(name,value):
    (OUT/name).write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')


def log(stage,**values):
    text=json.dumps({'at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'stage':stage,**values});print(text,flush=True)
    with (OUT/'driver.log').open('a') as f:f.write(text+'\n')


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
    return h.hexdigest()


def base():
    return ['docker','run','--rm','--pull','never','--no-healthcheck','--network','none','--read-only',
        '--cap-drop','ALL','--security-opt','no-new-privileges','--cpus','1','--memory','1536m',
        '--tmpfs','/tmp:rw,noexec,nosuid,size=32m']


def run(command,name,allow_stderr=False):
    save(name+'-command.json',command);log(name+'_start')
    r=subprocess.run(command,capture_output=True,text=True)
    (OUT/(name+'-stdout.log')).write_text(r.stdout);(OUT/(name+'-stderr.log')).write_text(r.stderr)
    save(name+'-exit.json',{'exit_code':r.returncode})
    if r.returncode or (r.stderr and not allow_stderr):raise RuntimeError(name+' failed; STOP without retry/recovery')
    log(name+'_PASS');return r


def observe(phase,name):
    gate=host.writer_gate();save('host-gate.json',gate);save(name+'-writer-before.json',gate)
    command=base()+['--mount','type=volume,src=trading-ai_scraper-data,dst=/data,readonly',
        '--mount',f'type=bind,src={OUT},dst=/proof,readonly','--env','SCRAPER_DOCUMENT_STORE_PATH=/data/snapshots',
        '--entrypoint','python',host.IMAGE,'/proof/observe.py',phase]
    result=json.loads(run(command,name).stdout);save(name+'.json',result);save(name+'-writer-after.json',host.writer_gate());return result


def seed(volume,source,expected,name):
    host.checked(['docker','volume','create','--label','org.trading-ai.disposable-proof=AR-01.3C',volume])
    command=base()+['--user','0:0','--mount',f'type=bind,src={source},dst=/input,readonly',
        '--mount',f'type=volume,src={volume},dst=/output',
        '--mount',f'type=bind,src={ROOT/"docs/acquisition-readiness/baselines/AR-01.3A"},dst=/proof,readonly',
        '--entrypoint','python',host.IMAGE,'/proof/copy_disposable.py',expected]
    run(command,name)


def fingerprint(volume,expected,name):
    evidence=OUT/name;evidence.mkdir();command=base()+['--user',f'{os.getuid()}:{os.getgid()}',
        '--mount',f'type=volume,src={volume},dst=/copy,readonly','--mount',f'type=bind,src={OUT},dst=/proof,readonly',
        '--mount',f'type=bind,src={evidence},dst=/evidence','--entrypoint','python',host.IMAGE,'/proof/fingerprint.py',expected]
    save(name+'-command.json',command);log(name+'_start')
    with (evidence/'stdout.log').open('w') as out,(evidence/'stderr.log').open('w') as err:r=subprocess.run(command,stdout=out,stderr=err)
    if r.returncode or (evidence/'stderr.log').stat().st_size:raise RuntimeError(name+' failed; no canary retry/recovery')
    log(name+'_PASS')


def verify_receipt(row,receipt):
    claim=receipt['claimed']['claim'];prepared=receipt['prepared'];released=receipt['released']
    expiry=(datetime.fromisoformat(prepared['now'])+timedelta(seconds=60)).isoformat()
    expected={**claim,'status':'RELEASED'}
    if row!=expected or released['record']!=expected or row['resource_key']!=KEY or row['owner']!=row['claim_token'] or row['lease_expires_at_utc']!=expiry or released['affected_rows']!=2:
        raise RuntimeError('Unexpected control row/lease/token/mutation receipt; STOP')


def main():
    if (OUT/'attempt.json').exists() or (OUT/'final.json').exists() or POST.exists():raise RuntimeError('Prior attempt exists; no retry')
    result={'status':'FAIL','pre_write_gate':'FAIL','write_attempted':False,'automatic_recovery':False,
        'database_restore':False,'operational_workers_started':False,'network_requests':0,'historical_backlog_changed':False}
    lock=(OUT/'host-single-attempt.lock').open('a')
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        log('START_AR013C_ATTEMPT02_ONLY')
        old=OUT.parent/'execution'; correction=OUT.parent/'launcher-attempt-02'
        preserved={}
        for package in (old,correction):
            count=0
            for line in (package/'SHA256SUMS').read_text().splitlines():
                digest,relative=line.split('  ',1)
                if sha(ROOT/relative)!=digest:raise RuntimeError('Reviewed evidence hash drift: '+relative)
                count+=1
            preserved[package.name]=count
        if sha(old/'attempt.json')!='241eb6f7c25256856dfbf587e32966625535c13ed4157f448f20ce5f56e18a7a':raise RuntimeError('Failed attempt marker differs')
        save('preserved-evidence.json',preserved)
        launcher_spec=importlib.util.spec_from_file_location('launcher02',correction/'launcher.py')
        launcher=importlib.util.module_from_spec(launcher_spec);launcher_spec.loader.exec_module(launcher)
        command=launcher.build_docker_argv()
        if command!=json.loads((correction/'corrected-docker-argv.json').read_text()):raise RuntimeError('Corrected explicit argv differs')
        launcher.validate_argv(command,command[41]);save('verified-docker-argv.json',command)
        config_hashes={'runtime-identity-core.json':'a9f83a2bde31c297fa5ff3b0f29bb12d150d4d4e28ce1af4b1a23d6308bdbc9d',
            'core-documents-sentinel.json':'2d0bf67aea44ecbc4ffa46582d4fae553d8440248d0cdd1debed81079f55e9d1',
            'store-sentinel.json':'bf885c5205ace8e3b68eacd4cf946a39353626e09fc3803aef5cbdc730ee641b'}
        for name,digest in config_hashes.items():
            if sha(OUT.parent/'approved-config'/name)!=digest:raise RuntimeError('Approved canary config drift: '+name)
        save('config-hashes.json',config_hashes)
        image=json.loads(host.checked(['docker','image','inspect',host.IMAGE]))[0]
        if image['Id']!=host.IMAGE.split('@')[1] or image['Config']['Labels'].get('org.opencontainers.image.revision')!='c3ddbbb9c298d07b8b46a362996251417282476a':raise RuntimeError('Image/source mismatch')
        save('image-identity.json',{'id':image['Id'],'labels':image['Config']['Labels']})
        volume=json.loads(host.checked(['docker','volume','inspect','trading-ai_scraper-data']))[0]
        if volume['Labels'].get('com.docker.compose.project')!='trading-ai':raise RuntimeError('Production volume identity differs')
        save('volume-identity.json',volume)
        names=host.checked(['docker','volume','ls','--format','{{.Name}}']).splitlines()
        if PREVOL in names or POSTVOL in names:raise RuntimeError('Disposable proof volume collision')
        raw=ROOT/'artifacts/acquisition-readiness/AR-01.3B/post-migration/raw'
        if sha(raw/'scraper.db')!=BASEHASH:raise RuntimeError('075 retained baseline hash differs')
        initial=observe('pre','initial-gate')
        # Retained full 157-table baseline is valid only after fresh production
        # byte identity and all historical evidence hashes have been proven.
        for p in (old/'before-fingerprints').iterdir():
            if p.is_file() and sha(p)!=sha(OUT/'before-fingerprints'/p.name):raise RuntimeError('Copied baseline drift')
        save('baseline-reuse.json',{'fresh_production_sha256':initial['db_sha256'],
            'original_full_baseline':str(old/'before-fingerprints'),
            'tables':157,'rows':62554200,'justification':'Fresh full DB hash exactly equals the retained fully fingerprinted 075 DB; previous Docker launch never ran application code'})
        pre=observe('pre','pre-write-gate')
        save('immediate-pre-write-owner-gate.json',host.writer_gate())
        result['pre_write_gate']='PASS'
        launcher.validate_argv(command,command[41])
        # Persistent marker precedes the only writable invocation. Lock stays
        # held through verification; no writes are replayed after interruption.
        save('attempt.json',{'key':KEY,'attempt':2,'execution_authorized':True,'authorization':'User explicit corrected attempt 02; once only','code_sha256':sha(OUT/'reviewed-inline-code.py'),
                            'recovery_authority':'USER','execution_agent':'CODEX','retry_authorized':False})
        save('canary-command.json',command)
        result['write_attempted']=True;start=time.time();mono=time.monotonic();log('CANARY_WRITE_START')
        r=subprocess.run(command,capture_output=True,text=True);exit_at=time.time()
        (OUT/'canary-stdout.log').write_text(r.stdout);(OUT/'canary-stderr.log').write_text(r.stderr)
        save('canary-timing.json',{'start_epoch':start,'exit_epoch':exit_at,'duration_seconds':time.monotonic()-mono,'exit_code':r.returncode})
        log('CANARY_WRITE_EXIT',exit_code=r.returncode)
        if r.returncode:raise RuntimeError('Canary stopped; preserve row/receipts, no retry or automatic release')
        receipt={item['phase']:item for item in [json.loads(s) for s in r.stdout.splitlines() if s.strip()]}
        if set(receipt)!={'prepared','claimed','released'}:raise RuntimeError('Incomplete logging; read-only verification only, do not repeat write')
        save('receipt.json',receipt)
        immediate=observe('post','immediate-observation');verify_receipt(immediate['control_row'],receipt)
        if immediate['other_claim_keys'] or immediate['physical_markers']!=pre['physical_markers']:raise RuntimeError('Other claims or physical marker changed')
        target=exit_at+120
        while time.time()<target:time.sleep(min(5,target-time.time()))
        end=observe('post','two-minute-observation');verify_receipt(end['control_row'],receipt)
        if end['db_before']!=immediate['db_before'] or end['db_sha256']!=immediate['db_sha256'] or end['other_claim_keys'] or end['physical_markers']!=pre['physical_markers']:raise RuntimeError('Observation-window drift')
        save('observation-window.json',{'exit_epoch':exit_at,'required_end_epoch':target,'observed_end_epoch':time.time(),
                                       'minimum_seconds':120,'source_unchanged':True})
        POST.mkdir(parents=True)
        capture=base()+['--user',f'{os.getuid()}:{os.getgid()}',
            '--mount','type=volume,src=trading-ai_scraper-data,dst=/source,readonly',
            '--mount',f'type=bind,src={POST},dst=/capture',
            '--mount',f'type=bind,src={ROOT/"docs/acquisition-readiness/baselines/AR-01.3B"},dst=/proof,readonly',
            '--entrypoint','python',host.IMAGE,'/proof/capture_state.py']
        captured=json.loads(run(capture,'post-capture').stdout);save('post-capture.json',captured)
        if list(captured['files'])!=['scraper.db'] or captured['files']['scraper.db']['sha256']!=end['db_sha256']:raise RuntimeError('Capture source/sidecar drift')
        seed(POSTVOL,POST,end['db_sha256'],'seed-post')
        fingerprint(POSTVOL,end['db_sha256'],'after-fingerprints')
        before=OUT/'before-fingerprints';after=OUT/'after-fingerprints'
        for name in ('schema.json','ledger.json'):
            if json.loads((before/name).read_text())!=json.loads((after/name).read_text()):raise RuntimeError('Schema/ledger changed')
        br=json.loads((before/'rows.json').read_text());ar=json.loads((after/'rows.json').read_text())
        changed=[t for t in br if br[t]!=ar[t]]
        if set(br)!=set(ar) or changed!=['document_extraction_claims']:raise RuntimeError('Unexpected other table fingerprint change')
        bclaims=json.loads((before/'claims.json').read_text());aclaims=json.loads((after/'claims.json').read_text())
        if bclaims or aclaims!=[end['control_row']]:raise RuntimeError('Other extraction-claim key changed')
        bf=json.loads((before/'foreign-keys.json').read_text());af=json.loads((after/'foreign-keys.json').read_text())
        new=sum((Counter(map(tuple,af))-Counter(map(tuple,bf))).values());missing=sum((Counter(map(tuple,bf))-Counter(map(tuple,af))).values())
        if new or missing or len(bf)!=10771:raise RuntimeError('Foreign-key identity drift')
        final=observe('post','final-readonly-gate');verify_receipt(final['control_row'],receipt)
        if final['db_sha256']!=end['db_sha256'] or final['db_before']!=end['db_before']:raise RuntimeError('Source drift during full proof')
        save('comparison.json',{'status':'PASS','all_tables':len(br),'unchanged_other_tables':len(br)-1,
            'changed_tables':changed,'new_fk_identities':new,'missing_fk_identities':missing,
            'before_fk':len(bf),'after_fk':len(af),'other_claim_keys_changed':0,'schema_ledger_unchanged':True})
        result.update({'status':'PASS','key':KEY,'normal_row_mutations':2,'final_control_status':'RELEASED',
            'unexpected_other_db_changes':0,'new_fk_identities':0,'source_sha256':end['db_sha256'],
            'control_row':end['control_row'],'full_other_table_preservation':True,'two_minute_observation':'PASS',
            'recovery_authority':'USER','execution_agent':'CODEX'})
        log('PASS_AR013C_NO_AR02_START');return 0
    except Exception as exc:
        result['error']=str(exc);log('STOP_NO_RETRY_NO_RECOVERY',error=str(exc))
        # No write recovery, release retry, receipt deletion or DB restore.
        # If usable, capture the current row read-only for the user.
        if result['write_attempted']:
            try:result['failure_readonly_state']=observe('post','failure-readonly-state')
            except Exception as observation_error:result['failure_observation_error']=str(observation_error)
        return 2
    finally:
        save('final.json',result);lock.close();print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':sys.exit(main())
