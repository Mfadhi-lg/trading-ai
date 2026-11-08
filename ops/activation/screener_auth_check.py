"""One selected four-endpoint check using the updated private Screener session."""
from datetime import datetime, timezone
import argparse
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
import subprocess
import nse_results as nse
from production_ownership import owner_gate

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--retain-content',action='store_true');args=parser.parse_args()
    nse.check_configuration()
    out=ROOT/'artifacts/screener-supplemental-access'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    out.mkdir(parents=True)
    compose=json.loads((ROOT/'artifacts/step3-financial-readiness/prepared/compose.json').read_text())
    service=compose['services']['current-financial-once']
    service['entrypoint']=['python','/activation/screener_auth_check_runtime.py']
    service['command']=[]
    if args.retain_content:
        os.chmod(out,0o700)
        nonce=os.urandom(32)
        proof={'nonce':nonce.hex(),'file_hmac':hmac.new(nonce,(ROOT/'.screener_session').read_bytes(),hashlib.sha256).hexdigest()}
        (out/'session-check.json').write_text(json.dumps(proof))
        os.chmod(out/'session-check.json',0o600)
        service['command']=['--retain-content']
        service['volumes'].append({'type':'bind','source':str(out),'target':'/probe','read_only':False})
    (out/'compose.json').write_text(json.dumps(compose,indent=2)+'\n')
    with (HERE/'.nse-owner.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        gate=owner_gate()
        image=json.loads(nse.checked(['docker','image','inspect',service['image']]))[0]
        nse.require(image['Id']==service['image'],'Existing canonical image mismatch')
        volume=json.loads(nse.checked(['docker','volume','inspect',nse.VOLUME]))[0]
        nse.require(volume['Labels'].get('com.docker.compose.project')=='trading-ai','Production volume mismatch')
        (out/'start.json').write_text(json.dumps({'owner_gate':gate,'endpoint_count':4,'financial_ingestion':False,'automatic_retry':False},indent=2)+'\n')
        command=['docker','compose','-f',str(out/'compose.json'),'--profile','current-financial',
            'run','--rm','--no-deps','--name','trading-ai-screener-auth-check','current-financial-once']
        with (out/'runtime.log').open('w') as log:
            result=subprocess.run(command,stdout=log,stderr=subprocess.STDOUT)
        outcome={'status':'PASS' if result.returncode==0 else 'STOP','exit_code':result.returncode,
            'owner_after':owner_gate(),'financial_ingestion':False,'automatic_retry':False}
        (out/'outcome.json').write_text(json.dumps(outcome,indent=2)+'\n')
        print(json.dumps({'status':outcome['status'],'path':str(out)}))
        return result.returncode


if __name__=='__main__':raise SystemExit(main())
