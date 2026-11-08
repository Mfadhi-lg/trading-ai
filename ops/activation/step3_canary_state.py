"""Read-only canary evidence, under the existing shared acquisition lock."""
import argparse
import fcntl
import json
from pathlib import Path
import subprocess
from production_ownership import owner_gate

HERE=Path(__file__).resolve().parent
OUT=HERE.parents[1]/'artifacts/step3-financial-canary-2026-10-09'


def main():
    global OUT
    p=argparse.ArgumentParser();p.add_argument('phase',choices=['before','after','source','details']);p.add_argument('--retry',action='store_true');args=p.parse_args()
    if args.retry:OUT=HERE.parents[1]/'artifacts/step3-financial-retry-2026-10-09'
    OUT.mkdir(parents=True,exist_ok=True)
    target=OUT/(args.phase+'.json')
    if target.exists():raise RuntimeError('Existing evidence must be preserved: '+str(target))
    with (HERE/'.nse-owner.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);owner=owner_gate()
        command=['docker','run','--rm','--network','none','--read-only','--cap-drop','ALL',
            '--mount','type=volume,source=trading-ai_scraper-data,target=/data'+(',readonly' if args.phase=='source' else ''),
            '--mount','type=bind,source='+str(HERE)+',target=/activation,readonly',
            '--mount','type=bind,source='+str(OUT)+',target=/verification,readonly',
            '--env','PYTHONDONTWRITEBYTECODE=1','--entrypoint','python',
            'sha256:1a310808f878911b4bd0a324beaf2948f215e3743238c981a531656cf0eecbf9',
            '/activation/step3_canary_source_runtime.py' if args.phase=='source' else
            '/activation/step3_accepted_details_runtime.py' if args.phase=='details' else '/activation/step3_canary_state_runtime.py']
        result=subprocess.run(command,capture_output=True,text=True,timeout=180)
        (OUT/(args.phase+'.stderr')).write_text(result.stderr)
        if result.returncode:raise RuntimeError(result.stderr)
        data=json.loads(result.stdout);target.write_text(json.dumps(data,indent=2)+'\n')
        (OUT/(args.phase+'-owner.json')).write_text(json.dumps(owner,indent=2)+'\n')
        print(json.dumps({'status':'PASS','phase':args.phase,'path':str(target)}))


if __name__=='__main__':main()
