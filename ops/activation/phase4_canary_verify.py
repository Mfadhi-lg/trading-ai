"""Bounded production read-only inspection using the existing image and locks."""
from datetime import datetime, timezone
import argparse
import fcntl
import json
from pathlib import Path
import subprocess

from production_ownership import owner_gate

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
OUT = ROOT / 'artifacts/phase4/canary/verification'
IMAGE = 'sha256:1a310808f878911b4bd0a324beaf2948f215e3743238c981a531656cf0eecbf9'


def main():
    global OUT
    parser = argparse.ArgumentParser()
    parser.add_argument('--output',type=Path)
    parser.add_argument('--detail', action='store_true')
    parser.add_argument('--fingerprint-preflight', action='store_true')
    parser.add_argument('--retry-preflight', action='store_true')
    parser.add_argument('--attempt-dir',type=Path,default=ROOT/'artifacts/phase4/canary')
    args = parser.parse_args()
    OUT=args.output.resolve() if args.output else OUT / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    if OUT.exists():raise RuntimeError("Preserve existing inspection; choose a new output directory")
    runtime = 'phase4_retry_preflight_runtime.py' if args.retry_preflight else 'phase4_fingerprint_preflight_runtime.py' if args.fingerprint_preflight else 'phase4_canary_verify_runtime.py'
    stem = 'production-detail' if args.detail else 'production-inspection'
    OUT.mkdir(parents=True, exist_ok=True)
    with (HERE / '.nse-owner.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = owner_gate()
        if args.fingerprint_preflight or args.retry_preflight:
            from phase4_documents import storage_preflight
            (OUT/'storage-preflight.json').write_text(json.dumps(storage_preflight(),indent=2)+'\n')
        command = ['docker','run','--rm','--name','trading-ai-step3-readonly','--network','none',
            '--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
            '--cpus','0.5','--memory','1536m','--tmpfs','/tmp:rw,noexec,nosuid,size=32m',
            '--mount','type=volume,source=trading-ai_scraper-data,target=/data,readonly',
            '--mount','type=bind,source='+str(HERE)+',target=/activation,readonly',
            '--env','PYTHONDONTWRITEBYTECODE=1','--entrypoint','python',IMAGE,
            '/activation/' + runtime]
        if args.fingerprint_preflight or args.retry_preflight:
            command[command.index('--env'):command.index('--env')] = ['--mount','type=bind,source='+str(args.attempt_dir.resolve())+',target=/selected,readonly']
        if args.retry_preflight:
            command[command.index('--env'):command.index('--env')] = ['--mount','type=bind,source='+str(ROOT/'scrapper')+',target=/app/scrapper,readonly']
        result = subprocess.run(command, capture_output=True, text=True, timeout=260)
        (OUT/(stem+'.stderr')).write_text(result.stderr)
        (OUT/(stem+'.json')).write_text(result.stdout)
        (OUT/'inspection-command.json').write_text(json.dumps(command,indent=2)+'\n')
        outcome = {'exit_code':result.returncode,'status':'PASS' if not result.returncode else 'STOP',
            'owner_before':before,'owner_after':owner_gate(),'finished_at_utc':datetime.now(timezone.utc).isoformat(),
            'production_mount':'read-only','source_requests':0,'worker_started':False}
        (OUT/(stem+'-outcome.json')).write_text(json.dumps(outcome,indent=2)+'\n')
        print(json.dumps({'status':outcome['status'],'path':str(OUT)}))
        if result.returncode:
            print(result.stderr)
            raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
