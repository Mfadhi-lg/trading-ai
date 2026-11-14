"""Checkpoint admission for the existing finite technical updater.

No timer is installed and no recurring acquisition grant is inferred. The host
may call tick after admitting an exact manifest. Saved receipts are idempotent.
"""
from datetime import datetime, timedelta
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from experiments.historical_candle_archive_v1.calendar import day_record
from experiments.historical_candle_archive_v1.storage import dump,sha
from experiments.historical_candle_archive_v1.control import checkpoint
from experiments.technical_fix_v2.update import OUT,MANIFEST,APPROVED,execute

IST=ZoneInfo('Asia/Kolkata')
CHECKPOINTS=('09:15','09:30','13:20','14:00','16:00')


def status(now=None,output=OUT):
    now=now or datetime.now(IST)
    if now.tzinfo is None:raise ValueError('aware checkpoint required')
    auth=output/'OWNER_AUTHORIZATION.json';ledger=output/'UPDATE_LEDGER.json'
    grant=json.loads(auth.read_text()) if auth.exists() else {}
    state=json.loads(ledger.read_text()) if ledger.exists() else {}
    recurring=output/'recurring'
    grant_path=recurring/'OWNER_GRANT.json';proposal=recurring/'PROPOSED_MANIFEST.json';timer=recurring/'TIMER_STATUS.json'
    approved=json.loads(grant_path.read_text()) if grant_path.exists() else {}
    timer_state=json.loads(timer.read_text()) if timer.exists() else {}
    from experiments.technical_fix_v2.schedule import status as scheduled_status
    scheduled = scheduled_status(now, output=recurring)
    authorized = scheduled['status'] == 'AUTHORIZED'
    enabled=authorized and timer_state.get('loaded',False)
    from scrapper.market_live.monitor import snapshot as live_status
    return dict(live_freshness=live_status(now=now), scheduled_updates=scheduled, checkpoints_ist=CHECKPOINTS,weekly='Reconcile only completed source intervals; special Sunday can extend the week',
        finite_batch_state=state.get('status','NOT_STARTED'),requests_saved=len(state.get('requests',{})),
        recurring_acquisition_enabled=enabled,recurring_trial_authorized=authorized,
        recurring_trial=approved if authorized else None,timer=timer_state,
        recurring_blocker='Bounded trial waits for HCD completion and approved dates; no renewal' if authorized else 'Owner approval covers a finite manifest only; no unbounded future requests',
        current_session=day_record(now.astimezone(IST).date()),
        manifest_sha256=grant.get('manifest_sha256'),last_error=state.get('last_error'))


def tick(now=None,*,output=OUT,manifest=MANIFEST,expected_digest=APPROVED,runner=execute):
    now=now or datetime.now(IST)
    if now.tzinfo is None:raise ValueError('aware checkpoint required')
    now=now.astimezone(IST);checkpoint()
    auth=output/'OWNER_AUTHORIZATION.json'
    if not auth.exists():return dict(status='BLOCKED_BY_MANIFEST_AUTHORIZATION')
    grant=json.loads(auth.read_text());digest=sha(manifest.read_bytes())
    if grant.get('manifest_sha256')!=digest or digest!=expected_digest:
        return dict(status='BLOCKED_BY_MANIFEST_AUTHORIZATION')
    session=day_record(now.date())
    if session['status']=='SPECIAL_LIVE_SECURITY_GRID_UNRESOLVED':
        return dict(status='BLOCKED_BY_SECURITY_SPECIFIC_SESSION_GRID',sources=session['source_refs'])
    if not session['sessions']:return dict(status='CLOSED_SESSION_NO_FETCH')
    due=[t for t in CHECKPOINTS if t<=now.strftime('%H:%M')]
    if not due:return dict(status='BEFORE_FIRST_CHECKPOINT_NO_FETCH')
    if due[-1]!='16:00':
        return dict(status='INTRADAY_SNAPSHOT_SCOPE_NOT_AUTHORIZED',
            reason='This finite manifest contains historical refreshes, not fresh live snapshots at each checkpoint')
    plan=json.loads(manifest.read_text())
    ends=[q.get('to_date',str(q.get('year',''))) for q in plan['requests'] if q['kind']!='TREASURY']
    # A finite past window cannot silently become a future rolling request.
    if ends and max(ends)<str(now.date()):
        return dict(status='BOUNDED_WINDOW_EXHAUSTED_NEW_MANIFEST_REQUIRED',through=max(ends))
    if ends and max(ends)>str(now.date()):return dict(status='FUTURE_REQUEST_WINDOW_NOT_DUE')
    ident=str(now.date())+'T'+due[-1]
    receipt=output/'scheduled-checkpoints'/(ident.replace(':','')+'.json')
    if receipt.exists():return json.loads(receipt.read_text())
    try:
        result=runner(authorize=digest,manifest_path=manifest,output=output,expected_digest=expected_digest)
    except BlockingIOError:return dict(status='UPDATER_ALREADY_RUNNING')
    record=dict(status='CHECKPOINT_BATCH_COMPLETE',checkpoint_ist=ident,executed_at=datetime.now(IST).isoformat(),
        manifest_sha256=digest,batch_status=result['status'],historical_receipts_backdated=False)
    dump(receipt,record);return record


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('action',choices=['status','tick']);args=p.parse_args()
    print(json.dumps(status() if args.action=='status' else tick(),indent=2,default=str))
