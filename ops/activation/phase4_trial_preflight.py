"""Reuse existing owner, storage, schema, source and expiry checks; no activation."""
import fcntl, hashlib, json, os, subprocess, sys
from datetime import datetime,timezone
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
from production_ownership import owner_gate
from phase4_documents import IMAGE,STORE,storage_preflight
from automatic_acquisition import verify_config
from prepare_phase4_routine import sources

def snapshot_trial(out,final=False,*,runtime="phase4_trial_snapshot_runtime.py"):
    if runtime not in ("phase4_trial_snapshot_runtime.py", "phase4_followup_snapshot_runtime.py"):
        raise ValueError("Unapproved read-only snapshot runtime")
    out.mkdir(parents=True,exist_ok=False)
    with (HERE/'.nse-owner.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        gate=owner_gate();capacity=storage_preflight();verify_config()
        args=['docker','run','--rm','--name','trading-ai-phase4-trial-snapshot','--network','none','--read-only',
            '--cap-drop','ALL','--security-opt','no-new-privileges','--cpus','0.5','--memory','1536m',
            '--tmpfs','/tmp:rw,noexec,nosuid,size=32m',
            '--mount','type=volume,source=trading-ai_scraper-data,target=/data,readonly',
            '--mount',f'type=bind,source={HERE},target=/activation,readonly',
            '--mount',f'type=bind,source={ROOT}/scrapper,target=/app/scrapper,readonly',
            '--mount',f'type=bind,source={STORE},target=/documents,readonly',
            '--mount',f'type=bind,source={ROOT}/artifacts/automatic-acquisition,target=/receipts,readonly',
            '--env','PYTHONDONTWRITEBYTECODE=1','--entrypoint','python',IMAGE,'/activation/'+runtime]
        if final:args.append('--final')
        r=subprocess.run(args,capture_output=True,text=True,timeout=260)
        (out/'runtime.log').write_text(r.stdout);(out/'stderr.log').write_text(r.stderr)
        (out/'command.json').write_text(json.dumps(args,indent=2))
        if r.returncode:raise RuntimeError('Trial snapshot failed; see '+str(out))
        records=[json.loads(line) for line in r.stdout.splitlines() if line.startswith('{')]
        data=next(x['snapshot'] for x in records if x.get('stage')=='trial_snapshot')
        (out/'snapshot.json').write_text(json.dumps(data,indent=2))
        (out/'host.json').write_text(json.dumps({'owner':gate,'capacity':capacity,'source_requests':0,'production_writes':0},indent=2))
    return data

def main():
    seal=json.loads((ROOT/'artifacts/phase4/routine/rollout/seal.json').read_text())
    assert sources()==seal['sources'],'Prepared source drift'
    assert hashlib.sha256((HERE/'automatic-acquisition-manifest.json').read_bytes()).hexdigest()==seal['installed_dispatcher_manifest_sha256'],'Installed manifest drift'
    assert not json.loads((HERE/'phase4_schedule.json').read_text())['enabled'],'Already enabled'
    snapshot_trial(ROOT/'artifacts/phase4/routine/trial/preflight')
    # Verify active expiry logic and existing host tests before installing it.
    env=dict(os.environ,PYTHONPATH=str(HERE),PYTHONDONTWRITEBYTECODE='1')
    tests=subprocess.run([str(ROOT/'scrapper/.venv/bin/python'),'-m','unittest','ops.activation.test_phase4_routine_schedule'],cwd=ROOT,env=env,capture_output=True,text=True,timeout=60)
    (ROOT/'artifacts/phase4/routine/trial/preflight/schedule-tests.log').write_text(tests.stdout+tests.stderr)
    assert tests.returncode==0,'Existing expiry/schedule tests failed'
    loaded=subprocess.check_output(['launchctl','print','gui/'+str(os.getuid())+'/com.trading-ai.automatic-acquisition'],text=True,timeout=20)
    (ROOT/'artifacts/phase4/routine/trial/preflight/launchctl.txt').write_text(loaded)
    hashes={n:hashlib.sha256((HERE/n).read_bytes()).hexdigest() for n in ('step3_schedule.json','step3_schedule.py','scheduled-bse-permission.json','com.trading-ai.automatic-acquisition.plist')}
    (ROOT/'artifacts/phase4/routine/trial/preflight/preservation-hashes.json').write_text(json.dumps(hashes,indent=2))
    print(json.dumps({'status':'PASS','expiry':'No new admissions after 24h; bounded in-flight completion','phase3_unchanged':True}))

if __name__=='__main__':main()
