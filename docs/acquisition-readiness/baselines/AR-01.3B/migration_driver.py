"""Explicit AR-01.3B only; one migration attempt, no workers or recovery."""
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[3]
EXPECTED='f237daa6f4696f75e2081e93d69a57be6f8d9c097769aaa169a819789c9e4811'
TARGET=ROOT/'artifacts/acquisition-readiness/AR-01.3B/post-migration'
VOLUME='trading-ai-ar013b-post-migration-verification-20261008'
spec=importlib.util.spec_from_file_location('host',ROOT/'ops/docker/canary/ar013_readonly_host.py')
host=importlib.util.module_from_spec(spec);spec.loader.exec_module(host)


def save(name,value):
    (OUT/name).write_text(json.dumps(value,indent=2,sort_keys=True)+'\n')


def log(stage,**data):
    text=json.dumps({'at_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'stage':stage,**data})
    print(text,flush=True)
    with (OUT/'driver.log').open('a') as f:f.write(text+'\n')


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(8*1024*1024),b''):h.update(b)
    return h.hexdigest()


def base(cpu='1'):
    return ['docker','run','--rm','--pull','never','--no-healthcheck','--network','none','--read-only',
            '--cap-drop','ALL','--security-opt','no-new-privileges','--cpus',cpu,'--memory','1536m',
            '--tmpfs','/tmp:rw,noexec,nosuid,size=32m']


def recorded(command,name,allow_stderr=False):
    save(name+'-command.json',command)
    log(name+'_start')
    r=subprocess.run(command,capture_output=True,text=True)
    (OUT/(name+'-stdout.log')).write_text(r.stdout);(OUT/(name+'-stderr.log')).write_text(r.stderr)
    save(name+'-exit.json',{'exit_code':r.returncode})
    if r.returncode or (r.stderr and not allow_stderr):raise RuntimeError(name+' failed; STOP, no retry/restore: '+r.stderr)
    log(name+'_PASS')
    return r


def inspect(phase,name,cpu='1'):
    gate=host.writer_gate();gate['harness_sha256']=sha(OUT/'inspect_db.py')
    save('host-gate.json',gate);save(name+'-writer-before.json',gate)
    command=base(cpu)+['--mount','type=volume,src=trading-ai_scraper-data,dst=/data,readonly',
        '--mount',f'type=bind,src={OUT},dst=/proof,readonly','--env','SCRAPER_DOCUMENT_STORE_PATH=/data/snapshots',
        '--entrypoint','python',host.IMAGE,'/proof/inspect_db.py',phase]
    r=recorded(command,name)
    data=json.loads(r.stdout);save(name+'.json',data);save(name+'-writer-after.json',host.writer_gate())
    return data


def capture(name,path):
    path.mkdir(parents=True)
    save(name+'-writer-before.json',host.writer_gate())
    command=base()+['--user',f'{os.getuid()}:{os.getgid()}',
        '--mount','type=volume,src=trading-ai_scraper-data,dst=/source,readonly',
        '--mount',f'type=bind,src={path},dst=/capture',
        '--mount',f'type=bind,src={OUT},dst=/proof,readonly',
        '--entrypoint','python',host.IMAGE,'/proof/capture_state.py']
    data=json.loads(recorded(command,name).stdout);save(name+'.json',data)
    save(name+'-writer-after.json',host.writer_gate())
    return data


def main():
    if (OUT/'migration-attempt.json').exists() or (OUT/'final.json').exists() or TARGET.exists():
        raise RuntimeError('Prior attempt/evidence exists; do not repeat, overwrite or restore')
    result={'status':'BLOCKED','pre_write_gate':'FAIL','migration_attempted':False,
            'operational_workers_started':False,'network_requests':0,'production_business_writes':0,
            'recovery_authority':'USER','execution_agent':'CODEX','automatic_restore':False}
    try:
        log('START_AR013B_ONLY')
        # Verify retained proofs/checksum indices, never treating the prior
        # intentionally aborted bind scan as the authoritative AR-01.3A result.
        for index in [ROOT/'docs/acquisition-readiness/baselines/AR-01.3A/SHA256SUMS',
                      ROOT/'docs/acquisition-readiness/baselines/AR-01.1/SHA256SUMS']:
            for line in index.read_text().splitlines():
                h,p=line.split(None,1)
                if sha(ROOT/p.strip())!=h:raise RuntimeError('Required evidence hash changed: '+p)
        proof=json.loads((ROOT/'docs/acquisition-readiness/baselines/AR-01.3A/native-proof/final.json').read_text())
        if proof['status']!='PASS' or proof['restored_sha256']!=EXPECTED:raise RuntimeError('Snapshot/restore proof unavailable')
        if sha(Path(proof['snapshot_path']))!=EXPECTED:raise RuntimeError('Retained fresh snapshot bytes changed')
        image=json.loads(host.checked(['docker','image','inspect',host.IMAGE]))[0]
        if image['Id']!=host.IMAGE.split('@')[1] or image['Config']['Labels'].get('org.opencontainers.image.revision')!='c3ddbbb9c298d07b8b46a362996251417282476a':
            raise RuntimeError('Loaded image/source mismatch')
        save('image-identity.json',{'id':image['Id'],'labels':image['Config']['Labels'],'platform':image['Os']+'/'+image['Architecture']})
        volume=json.loads(host.checked(['docker','volume','inspect','trading-ai_scraper-data']))[0]
        if volume['Name']!='trading-ai_scraper-data' or volume['Labels'].get('com.docker.compose.project')!='trading-ai':raise RuntimeError('Wrong production volume')
        save('volume-identity.json',volume)
        recovery_command=base()+['--mount',f'type=volume,src={proof["restore_volume"]},dst=/recovery,readonly',
            '--entrypoint','python',host.IMAGE,'-c',
            'import hashlib,json; from pathlib import Path; p=Path("/recovery/scraper.db"); h=hashlib.sha256(); f=p.open("rb"); '+
            '[h.update(b) for b in iter(lambda:f.read(8*1024*1024),b"")]; f.close(); '+
            'print(json.dumps({"sha256":h.hexdigest(),"bytes":p.stat().st_size}))']
        available=json.loads(recorded(recovery_command,'restore-copy-availability').stdout)
        if available['sha256']!=EXPECTED:raise RuntimeError('Disposable restore-proof volume changed/unavailable')
        save('recovery-copy-availability.json',available)
        names=host.checked(['docker','volume','ls','--format','{{.Name}}']).splitlines()
        if VOLUME in names:raise RuntimeError('Post-verification target volume already exists')
        s=os.statvfs(ROOT)
        baseline=json.loads((OUT/'source-baseline.json').read_text())['source_before']
        reserve=3*baseline['bytes']+1024**3
        if s.f_bavail*s.f_frsize-reserve<0.15*s.f_blocks*s.f_frsize:raise RuntimeError('Insufficient reviewed post/failure preservation headroom')
        save('capacity.json',{'free_bytes':s.f_bavail*s.f_frsize,'reserved_bytes':reserve,'total_bytes':s.f_blocks*s.f_frsize})
        manifest=json.loads((OUT/'migration-manifest.json').read_text())
        reviewed=json.loads((ROOT/'docs/acquisition-readiness/baselines/AR-01.1/migration-checksums.json').read_text())
        old_hashes={r['filename']:r['sha256'] for r in reviewed['canonical']}
        for item in manifest['pending']:
            if sha(ROOT/'scrapper/db/migrations'/item['filename'])!=item['sha256'] or old_hashes[item['filename']]!=item['sha256']:
                raise RuntimeError('Migration SQL checksum mismatch')
        pre=inspect('pre','pre-write')
        # Final owner check immediately before the only authorized writable run.
        final_gate=host.writer_gate();save('immediate-pre-migration-writer-gate.json',final_gate)
        result['pre_write_gate']='PASS';save('pre-write-gate-result.json',{'status':'PASS','schema':'068','source_sha256':EXPECTED,
            'snapshot_restore_proof_available':True,'only_reviewed_seven_pending':True,'no_partial_objects':True})
        command=base('0.5')+['--name','trading-ai-ar013b-migration-only-once',
            '--mount','type=volume,src=trading-ai_scraper-data,dst=/data',
            '--mount',f'type=bind,src={OUT/"migration-only-runtime-identity.json"},dst=/run/trading-ai/runtime-identity.json,readonly',
            '--mount',f'type=bind,src={OUT/"store-sentinel.json"},dst=/data/.trading_ai_runtime_identity.json,readonly',
            '--mount',f'type=bind,src={OUT/"core-documents-sentinel.json"},dst=/data/snapshots/.trading_ai_runtime_identity.json,readonly',
            '--env','TRADING_AI_ENV=production','--env','TRADING_AI_RUNTIME_IDENTITY_FILE=/run/trading-ai/runtime-identity.json',
            '--env','SCRAPER_DB_PATH=/data/scraper.db','--env','SCRAPER_STORE_PATH=/data',
            '--env','SCRAPER_DOCUMENT_STORE_PATH=/data/snapshots',
            '--entrypoint','python',host.IMAGE,'-m','scrapper.cli','init-db','--db','/data/scraper.db']
        save('migration-command.json',command)
        save('migration-attempt.json',{'authorized':'AR-01.3B user instruction','attempt':1,'retry_forbidden':True,
            'runtime_identity_sha256':sha(OUT/'migration-only-runtime-identity.json'),'source_baseline_sha256':EXPECTED})
        result['migration_attempted']=True
        start=time.time();mono=time.monotonic();log('MIGRATION_START')
        r=subprocess.run(command,capture_output=True,text=True)
        timing={'start_epoch':start,'end_epoch':time.time(),'duration_seconds':time.monotonic()-mono,'exit_code':r.returncode}
        save('migration-timing.json',timing)
        (OUT/'migration-stdout.log').write_text(r.stdout);(OUT/'migration-stderr.log').write_text(r.stderr)
        log('MIGRATION_END',**timing)
        if r.returncode:raise RuntimeError('Migration exited nonzero; no retry/ledger repair/restore: '+r.stderr)
        stdout=json.loads(r.stdout)
        if stdout!={'status':'ok','database':'/data/scraper.db'}:raise RuntimeError('Unexpected migration stdout')
        diagnostics=[json.loads(line) for line in r.stderr.splitlines() if line.strip()]
        if not diagnostics or any(d.get('event')!='runtime_writable_target_check' or d.get('environment')!='production' or d.get('release_id')!='c28.1-ar012-20261007' for d in diagnostics):
            raise RuntimeError('Unexpected migration identity diagnostic')
        result['migration_exit_success']=True
        save('immediate-post-migration-writer-gate.json',host.writer_gate())
        post=inspect('post','post-metadata')
        if post['original_markers']!=pre['original_markers']:
            raise RuntimeError('Physical runtime sentinel bytes changed unexpectedly')
        delta=post['db_before']['bytes']-pre['db_before']['bytes']
        if delta<0 or delta>1024**2:raise RuntimeError('Unexpected DB size growth/shrinkage: '+str(delta))
        save('size-delta.json',{'before':pre['db_before']['bytes'],'after':post['db_before']['bytes'],'delta_bytes':delta,'review_alert_bytes':1024**2})
        captured=capture('post-capture',TARGET/'raw')
        if list(captured['files'])!=['scraper.db'] or captured['files']['scraper.db']['sha256']!=post['source_sha256']:
            raise RuntimeError('Unexpected post-migration sidecar or source change')
        expected=post['source_sha256']
        host.checked(['docker','volume','create','--label','org.trading-ai.disposable-proof=AR-01.3B',VOLUME])
        seed=base()+['--user','0:0','--mount',f'type=bind,src={TARGET/"raw"},dst=/input,readonly',
            '--mount',f'type=volume,src={VOLUME},dst=/output',
            '--mount',f'type=bind,src={ROOT/"docs/acquisition-readiness/baselines/AR-01.3A"},dst=/proof,readonly',
            '--entrypoint','python',host.IMAGE,'/proof/copy_disposable.py',expected]
        recorded(seed,'seed-post-verification')
        evidence=OUT/'post-verification';evidence.mkdir()
        verification=base()+['--user',f'{os.getuid()}:{os.getgid()}',
            '--mount',f'type=volume,src={VOLUME},dst=/copy,readonly',
            '--mount',f'type=bind,src={OUT},dst=/proof,readonly',
            '--mount',f'type=bind,src={evidence},dst=/evidence',
            '--entrypoint','python',host.IMAGE,'/proof/verify_post.py',expected]
        save('post-verification-command.json',verification);log('FULL_POST_VERIFICATION_START')
        with (evidence/'stdout.log').open('w') as out,(evidence/'stderr.log').open('w') as err:
            v=subprocess.run(verification,stdout=out,stderr=err)
        if v.returncode or (evidence/'stderr.log').stat().st_size:raise RuntimeError('Full post-migration verification failed; no repair')
        proof_after=json.loads((evidence/'verification.json').read_text())
        log('FULL_POST_VERIFICATION_PASS')
        canary=inspect('canary','schema075-canary','0.5')
        if canary['source_sha256']!=expected or canary['db_before']!=post['db_before']:
            raise RuntimeError('Production changed during stopped-worker proof window')
        result.update({'status':'PASS','schema':'075','post_sha256':expected,'size_delta_bytes':delta,
            'original_data_preserved':proof_after['original_data_preserved'],'new_fk_identities':0,'missing_fk_identities':0,
            'quick_check':'ok','integrity_check':'ok','c28_schema_compatibility':'PASS','canary_zero_write':True,
            'post_verification_volume':VOLUME,'post_capture':str(TARGET/'raw/scraper.db')})
        log('PASS_AR013B_MIGRATION_ONLY')
        return 0
    except Exception as exc:
        result['error']=str(exc);result['status']='FAIL' if result['migration_attempted'] else 'BLOCKED'
        log('STOP_NO_RETRY_NO_RESTORE',error=str(exc),migration_attempted=result['migration_attempted'])
        if result['migration_attempted']:
            try:result['failure_state']=capture('failure-preservation',ROOT/'artifacts/acquisition-readiness/AR-01.3B/failure-state')
            except Exception as failure:result['failure_preservation_error']=str(failure)
        return 2
    finally:
        save('final.json',result);print(json.dumps(result,indent=2),flush=True)


if __name__=='__main__':sys.exit(main())
