"""Guarded read-only offline benchmark; never activates production schedules."""
import fcntl, json, subprocess, sys, time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'ops/activation'))
from production_ownership import owner_gate
from phase4_documents import IMAGE, STORE, storage_preflight

def main():
    mode='metrics' if '--metrics-only' in sys.argv else 'census' if '--census-only' in sys.argv else None
    out=ROOT/'artifacts/phase4/throughput-review'/('offline-'+mode if mode else 'offline')
    out.mkdir(parents=True,exist_ok=False)
    name='trading-ai-phase4-offline-throughput'
    with (ROOT/'ops/activation/.nse-owner.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        gate=owner_gate();capacity=storage_preflight()
        command=['docker','run','--name',name,'--network','none','--read-only','--cap-drop','ALL',
            '--security-opt','no-new-privileges','--cpus','0.5','--memory','1536m',
            '--tmpfs','/tmp:rw,nosuid,size=512m',
            '--mount','type=volume,source=trading-ai_scraper-data,target=/data,readonly',
            '--mount',f'type=bind,source={STORE},target=/documents,readonly',
            '--mount',f'type=bind,source={ROOT}/ops/activation,target=/activation,readonly',
            '--mount',f'type=bind,source={ROOT}/ops/benchmarks,target=/bench,readonly',
            '--mount',f'type=bind,source={ROOT}/scrapper,target=/app/scrapper,readonly',
            '--mount',f'type=bind,source={out},target=/output',
            '--env','PYTHONDONTWRITEBYTECODE=1','--env','OMP_THREAD_LIMIT=1',
            '--entrypoint','python',IMAGE,'/bench/phase4_throughput.py']
        if mode: command.append('--'+mode+'-only')
        (out/'command.json').write_text(json.dumps(command,indent=2))
        start=time.monotonic()
        try:
            with (out/'runtime.log').open('w') as log:
                run=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT,timeout=800)
            receipt={'exit_code':run.returncode,'seconds':time.monotonic()-start,'owner_before':gate,
                'capacity':capacity,'source_requests':0,'production_writes':0,'database_mount':'read-only','archive_mount':'read-only'}
            (out/'receipt.json').write_text(json.dumps(receipt,indent=2))
            print(json.dumps({'exit_code':run.returncode,'path':str(out)}))
        finally:
            subprocess.run(['docker','stop','-t','5',name],capture_output=True,timeout=30)
            subprocess.run(['docker','rm',name],capture_output=True,timeout=30)

if __name__=='__main__':main()
