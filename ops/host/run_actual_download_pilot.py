"""Dispatch finite current-work pilot slots through the installed controller."""
import argparse
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'ops/activation'),str(ROOT/'ops/host'),str(ROOT)]
import automatic_acquisition as scheduler
from acquisition_lifecycle import lock
from document_backlog_recovery import window_safe
from scrapper.sources.exchange.reporting_periods import IST


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--stage',choices=('download','financial','processing','rating'),default='download')
    p.add_argument('--count',type=int,default=1)
    p.add_argument('--wait',action='store_true',help='Wait only for existing owner and scheduler admission windows')
    p.add_argument('--not-before-utc',help='Honor an evidenced host cooldown, without claims or clock replay')
    p.add_argument('--historical-plan',type=Path)
    p.add_argument('--authorize-plan-sha256',help='Use only after explicit approval of the exact finite campaign')
    p.add_argument('--resolve-own-attestation-stop',action='store_true')
    p.add_argument('--current-saved-parse-ids',default='',help='At most eight accepted current-quarter saved-page parse IDs')
    a=p.parse_args()
    if a.not_before_utc:
        due=datetime.fromisoformat(a.not_before_utc)
        remaining=(due-datetime.now(timezone.utc)).total_seconds()
        if remaining>1800:raise ValueError('Pilot host cooldown wait is bounded to 30 minutes')
        if remaining>0:
            raise BlockingIOError('Evidenced source cooldown until '+due.isoformat())
    if not 1<=a.count<=10:raise ValueError('Pilot capacity is 1–10 slots')
    campaign=None
    saved_ids=tuple(int(x) for x in a.current_saved_parse_ids.split(',') if x)
    if saved_ids and (a.stage!='download' or len(saved_ids)>8):raise ValueError('Bounded download saved-page repair only')
    if a.historical_plan:
        payload=a.historical_plan.read_bytes()
        if hashlib.sha256(payload).hexdigest()!=a.authorize_plan_sha256:
            raise ValueError('Explicit approval must identify the exact historical plan SHA-256')
        campaign=json.loads(payload)
        if a.stage!='download' or a.count!=len(campaign['jobs']):raise ValueError('Exact campaign job count required')
    out=ROOT/'artifacts/acquisition-actual-download-recovery/raw'
    for n in range(a.count):
        with lock(ROOT/'ops/activation/.scheduler-admission.lock'):
            window_safe(120)
            scheduler.verify_config()
            if a.resolve_own_attestation_stop:
                stopped=scheduler.STATE/'DOCUMENT_DOWNLOAD.stopped.json'
                old=scheduler.STATE/'2026-10-30-actual-download-repair-download-1-1712'
                if stopped.exists():
                    from production_ownership import owner_gate
                    with lock(ROOT/'ops/activation/.nse-owner.lock'):
                        owner_gate()
                        failure=json.loads(stopped.read_text())
                        known=('Runtime drift' in (old/'runtime.log').read_text() if not failure.get('error') else
                            failure['error']=="cannot access local variable 'jobs' where it is not associated with a value")
                        if failure.get('job')!='actual-download-repair-download-1' or not known:
                            raise ValueError('Only this pilot attestation stop may be resolved')
                        stopped.rename(out/('resolved-own-stop-'+datetime.now(IST).strftime('%Y%m%dT%H%M%S')+'.json'))
            kind='FINANCIAL' if a.stage=='financial' else 'DOCUMENT_'+a.stage.upper()
            stamp=datetime.now(IST)
            name='actual-download-repair-'+a.stage+'-'+str(n+1)
            execute=None
            if campaign:
                from phase4_schedule import execute_once
                one={**campaign,'jobs':[campaign['jobs'][n]],'max_requests':1,
                    'authorization_reference':'Explicit operator approval of SHA-256 '+a.authorize_plan_sha256}
                execute=lambda kind,folder:execute_once(kind,folder,recovery_campaign=one)
            elif saved_ids:
                from phase4_schedule import execute_once
                execute=lambda kind,folder:execute_once(kind,folder,saved_parse_ids=saved_ids)
            result=scheduler.run_slot(name,kind,stamp,execute=execute)
            receipt=out/(name+'-'+stamp.strftime('%Y%m%dT%H%M%S')+'.json')
            scheduler.save(receipt,result)
            print(json.dumps(result,default=str),flush=True)
            if result.get('status') not in ('PASS','FAILED_JOB','BLOCKED_SOURCE_POLICY'):break


if __name__=='__main__':
    for attempt in range(120 if '--wait' in sys.argv else 1):
        try:
            main();break
        except (BlockingIOError,RuntimeError) as exc:
            if '--wait' not in sys.argv or not (isinstance(exc,BlockingIOError) or 'Active owner has not drained' in str(exc)):
                raise
            print(json.dumps({'admission_deferred':str(exc),'source_requests':0}),flush=True)
            time.sleep(10)
    else:raise RuntimeError('Bounded pilot admission wait exhausted')
