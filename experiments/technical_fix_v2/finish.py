"""Finish the existing finite campaign's chart/review handoff after acquisition.

No new provider requests, model work, or recurring grants are created here.
"""
from collections import Counter,defaultdict
from datetime import datetime,timezone
import csv
import fcntl
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import time

from experiments.historical_candle_archive_v1.storage import ROOT,dump,sha
from .update import OUT

HCD=ROOT/'artifacts/historical-candle-archive-v1/hcd02'


def snapshot():
    inventory=json.loads((HCD/'full-inventory.json').read_text())
    groups=defaultdict(list)
    for row in inventory['timeframes']:groups[row['company_id']].append(row)
    unavailable=[];pending=[]
    for cid,rows in groups.items():
        empty=[r['timeframe'] for r in rows if r['all_planned_requests_successful'] and not r['returned_nonempty_history']]
        if empty:unavailable.append(dict(company_id=cid,timeframes=empty,reason='PROVIDER_RETURNED_NO_ROWS_IN_ALL_PLANNED_WINDOWS',identity_and_listing_review_required=True))
        if any(not r['all_planned_requests_successful'] for r in rows):pending.append(cid)
    dump(OUT/'PROVIDER_EMPTY_HISTORIES.json',dict(asof=inventory['created_at'],companies=len(unavailable),histories=unavailable,meaning='Observed provider absence only; not proof that an instrument never traded. Unrequested windows are pending, never unavailable.'))
    campaign=json.loads((HCD/'full-campaign.json').read_text())
    repairs=json.loads((HCD/'repair-queue-summary.json').read_text())
    result=dict(asof=inventory['created_at'],campaign_status=campaign['status'],phase=campaign['phase'],
        companies_completed=inventory['companies_with_all_four_planned_native_histories'],expected_companies=1902,
        successful_native_requests=inventory['successful_requests'],failed_native_requests=inventory['failed_requests'],
        native_requests_remaining=83688-inventory['successful_requests']-inventory['failed_requests'],
        companies_with_pending_or_failed_windows=len(pending),provider_empty_histories=len(unavailable),
        quality_exceptions=repairs,quality_snapshot_is_separate_from_acquisition_progress=True,
        schedules=dict(authorized_manifest_sha256='06604ec82060a0468a515fa30a4c0f0da6943e99a02810ea43d866dd172acfac',first_day='2026-11-09',last_day='2026-11-13',max_requests=48122,automatic_renewal=False),
        full_universe_finished=campaign.get('phase')=='FINISHED',
        completion_definition='All planned requests successful and nonempty native history for each of four timeframes; numerical/session exceptions remain separately quarantined')
    dump(OUT/'CONTINUATION_STATUS.json',result)
    return result


def run_step(name,args,env):
    with (OUT/(name+'.log')).open('wb') as log:
        subprocess.run(args,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,check=True)


def recover_local_stop(state):
    """Only SQLite failures with provable local recovery, never unknown delivery."""
    if state.get('reason')!='OperationalError':return dict(status='EXTERNAL_OR_NONLOCAL_STOP; NO_AUTOMATIC_RETRY')
    from experiments.historical_candle_archive_v1.access import HistoricalStore
    from ops.host.historical_candle_lifecycle import wake
    approved=set()
    for path in (HCD.parent/'full-universe-manifest.json',HCD/'special-full-manifest.json'):
        approved.update(q['request_id'] for q in json.loads(path.read_text())['requests'])
    store=HistoricalStore()
    try:
        rows=store.db.execute("SELECT request_id,status,raw_path,attempts FROM requests WHERE status IN ('STOPPED','IN_FLIGHT','RAW_SAVED')").fetchall()
        held=[r for r in rows if r['request_id'] in approved]
        if not held:return dict(status='NO_PROVABLE_LOCAL_RECOVERY')
        for row in held:
            receipt=store.root/'receipts'/(row['request_id']+'.json')
            # Without immutable response bytes, transmission cannot be inferred
            # from an absent transport log after a crash. Never repeat that GET.
            if not row['raw_path'] and not receipt.exists():
                return dict(status='UNCERTAIN_PROVIDER_DELIVERY; OWNER_REVIEW_REQUIRED')
    finally:store.close()
    return wake()


def watch():
    with (OUT/'.completion-owner.lock').open('a+b') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        dump(OUT/'COMPLETION_WORKER.json',dict(pid=os.getpid(),state='WAITING_FOR_AUTHORIZED_BACKFILL',started_at=datetime.now(timezone.utc).isoformat()))
        recoveries=[]
        while True:
            state=json.loads((HCD/'full-campaign.json').read_text())
            snapshot()
            if state.get('phase')=='FINISHED' and (HCD/'owner-repair-report-ready.json').exists():break
            if state['status']=='STOPPED':
                recovery=recover_local_stop(state) if len(recoveries)<3 else dict(status='BOUNDED_RECOVERY_EXHAUSTED')
                if recovery.get('status')=='RUNNING':
                    recoveries.append(dict(at=datetime.now(timezone.utc).isoformat(),result=recovery))
                    dump(OUT/'CONTINUATION_RECOVERIES.json',recoveries)
                dump(OUT/'COMPLETION_WORKER.json',dict(pid=os.getpid(),state='WAITING_FOR_AUTHORIZED_BACKFILL' if recovery.get('status')=='RUNNING' else 'BLOCKED_BY_CAMPAIGN_STOP',reason=state.get('reason'),recovery=recovery,automatic_provider_retry=False))
            time.sleep(30)
        env=os.environ.copy()
        runtime=json.loads((HCD/'candle-runtime.json').read_text());python=runtime['python']
        env['PYTHONPATH']=runtime['dependencies']+os.pathsep+str(ROOT)
        try:
            run_step('FINAL_CHART_EXPORT',[python,'-B','-m','experiments.technical_fix_v2.integrate'],env)
            run_step('FINAL_SOURCE_VERIFICATION',[python,'-B','-m','experiments.technical_fix_v2.verify'],env)
            run_step('FINAL_BROWSER_VALIDATION',[json.loads((OUT/'CONTINUATION_RUNTIME.json').read_text())['node'],str(ROOT/'experiments/technical_fix_v2/browser.mjs')],env)
            snapshot()
            run_step('FINAL_PORTABLE_PACKAGE',[python,'-B','-m','experiments.technical_fix_v2.portable'],env)
            dump(OUT/'COMPLETION_WORKER.json',dict(state='FINISHED',completed_at=datetime.now(timezone.utc).isoformat(),provider_requests=0))
        except BaseException as error:
            dump(OUT/'COMPLETION_WORKER.json',dict(state='HANDOFF_FAILED',error_type=type(error).__name__,provider_requests=0))
            raise

if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='watch':watch()
    else:print(json.dumps(snapshot(),indent=2))
