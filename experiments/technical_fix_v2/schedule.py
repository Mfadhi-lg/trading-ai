"""Bounded recurring proposal and explicit grant gate. Never installs a timer."""
from datetime import datetime, timedelta
import fcntl
import json
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from experiments.historical_candle_archive_v1.calendar import day_record
from experiments.historical_candle_archive_v1.storage import ROOT, dump, sha
from .update import execute, get

OUT = ROOT/'artifacts/technical-fix-v2/recurring'
IST = ZoneInfo('Asia/Kolkata')
TIMES = ('09:15', '09:30', '13:20', '14:00', '16:00')


def prepare(output=OUT):
    original=json.loads((ROOT/'artifacts/historical-candle-archive-v1/full-universe-manifest.json').read_text())
    companies={c['company_id']:c for c in original['companies']+original.get('historically_eligible_extension',[]) if c.get('instrument_key')}
    eligible={q['company_id'] for q in original['requests']}
    companies={k:v for k,v in companies.items() if k in eligible}
    assert len(companies)==1902
    finite=ROOT/'artifacts/technical-fix-v2'
    old=json.loads((finite/'BOUNDED_ACQUISITION_MANIFEST.json').read_text())
    ledger=json.loads((finite/'UPDATE_LEDGER.json').read_text())['requests']
    contexts=[q for q in old['requests'] if q['kind'] in ('NSE_INDEX','GLOBAL','TREASURY') and ledger[q['request_id']]['status']=='SUCCESS']
    requests=[]
    for n in range(5):
        day='2026-11-'+str(9+n).zfill(2)
        if day_record(day)['status']!='REGULAR_SCHEDULED':continue
        previous=str(datetime.fromisoformat(day).date()-timedelta(days=1))
        for at in TIMES:
            for c in companies.values():
                specs=(('days','Daily'),) if at=='09:15' else ((('days','Daily'),('hours','1-hour'))+((('weeks','Weekly'),) if day.endswith('13') else ()) if at=='16:00' else (('hours','1-hour'),))
                for unit,tf in specs:
                    target=previous if at=='09:15' else day
                    start=str(datetime.fromisoformat(target).date()-timedelta(days=7)) if unit in ('weeks','days') else target
                    url='https://api.upstox.com/v3/historical-candle/'
                    intraday=at not in ('09:15','16:00')
                    if intraday:url+='intraday/'+quote(c['instrument_key'],safe='')+'/hours/1'
                    else:url+=quote(c['instrument_key'],safe='')+'/'+unit+'/1/'+target+'/'+start
                    q=dict(kind='STOCK',series=c['symbol'],company_id=c['company_id'],instrument_key=c['instrument_key'],unit=unit,interval=1,timeframe=tf,from_date=start,to_date=target,url=url,day=day,checkpoint=at,intraday=intraday)
                    q['request_id']=sha(json.dumps(q,sort_keys=True).encode());requests.append(q)
            if at=='16:00':
                for source in contexts:
                    q={k:v for k,v in source.items() if k not in ('request_id','identity')}
                    q.update(day=day,checkpoint=at,intraday=False,to_date=day)
                    if q['kind']!='TREASURY':
                        q['from_date']=previous
                        q['url']='https://api.upstox.com/v3/historical-candle/'+quote(q['instrument_key'],safe='')+'/days/1/'+day+'/'+previous
                    q['request_id']=sha(json.dumps(q,sort_keys=True).encode());requests.append(q)
    plan=dict(status='PREPARED_NOT_AUTHORIZED',first_day='2026-11-09',last_day='2026-11-13',timezone='Asia/Kolkata',max_requests=len(requests),automatic_retries=0,max_new_spend_usd=0,minimum_request_spacing_seconds=2,checkpoints_ist=TIMES,companies=len(companies),context_series=len(contexts),requests=requests,notes=['Finite four-session trial; no renewal or paid upgrade','Queue starts are not universe completion deadlines','No intraday replay on a later date; no receipt backdating','4-hour bars derived by existing session-aware access layer','Wait for historical campaign and quota cooldown; no parallel acquisition'])
    dump(output/'PROPOSED_MANIFEST.json',plan)
    digest=sha((output/'PROPOSED_MANIFEST.json').read_bytes())
    dump(output/'AUTHORIZATION_REQUEST.json',dict(manifest_sha256=digest,max_requests=len(requests),first_day=plan['first_day'],last_day=plan['last_day'],new_spend_usd=0,automatic_retries=0,installed=False,enabled=False,authorization_required='Approve exact manifest digest and four-session window; credential renewal remains with owner'))
    return dict(digest=digest,requests=len(requests),companies=len(companies),contexts=len(contexts))


def status(now=None, *, output=OUT):
    """Read receipts only; never open the archive, credentials, or provider."""
    now = now or datetime.now(IST)
    if now.tzinfo is None:
        raise ValueError('aware checkpoint required')
    now = now.astimezone(IST)
    plan_path = output/'PROPOSED_MANIFEST.json'
    grant_path = output/'OWNER_GRANT.json'
    if not plan_path.exists():
        return dict(status='NOT_CONFIGURED', freshness='MISSING', batches=[])
    raw = plan_path.read_bytes()
    plan = json.loads(raw)
    grant = json.loads(grant_path.read_text()) if grant_path.exists() else {}
    authorized = (grant.get('manifest_sha256') == sha(raw)
                  and grant.get('max_requests') == plan['max_requests'])
    groups = {}
    for q in plan['requests']:
        if (q['day'], q['checkpoint']) <= (str(now.date()), now.strftime('%H:%M')):
            groups.setdefault((q['day'], q['checkpoint']), []).append(q)
    batches = []
    latest = None
    accepted = 0
    for (day, at), queries in sorted(groups.items()):
        ledger_path = output/'batches'/(day+'T'+at.replace(':', ''))/'UPDATE_LEDGER.json'
        ledger = json.loads(ledger_path.read_text()) if ledger_path.exists() else {}
        scope_error = None
        if ledger:
            manifest = ledger_path.with_name('manifest.json')
            if not manifest.exists():
                scope_error = 'SAVED_BATCH_MANIFEST_MISSING'
            elif (json.loads(manifest.read_text()) != dict(requests=queries, parent_manifest_sha256=sha(raw))
                  or ledger.get('manifest_sha256') != sha(manifest.read_bytes())):
                scope_error = 'SAVED_BATCH_SCOPE_CHANGED'
        receipts = [ledger.get('requests', {}).get(q['request_id'], {}) if not scope_error else {} for q in queries]
        for rec in receipts:
            if rec.get('status') == 'SUCCESS':
                accepted += rec.get('accepted', 0)
                value = rec.get('latest_valid_observation_at')
                if value:
                    observed = datetime.fromisoformat(value)
                    latest = max(latest, observed) if latest else observed
        uncertain = any(r.get('status') == 'IN_FLIGHT' and not r.get('raw_path') for r in receipts)
        unresolved = any(r and r.get('status') not in ('SUCCESS', 'EMPTY', 'ISOLATED_INSTRUMENT_REJECTION', 'RAW_SAVED') for r in receipts)
        http_error = any(r.get('http_status', 200) != 200 and r.get('status') != 'ISOLATED_INSTRUMENT_REJECTION' for r in receipts)
        saved = any(r.get('status') == 'RAW_SAVED' and r.get('http_status') == 200 for r in receipts)
        batches.append(dict(day=day, checkpoint=at, status='SCOPE_MISMATCH' if scope_error else ledger.get('status', 'NOT_STARTED'),
            requests=len(queries), successful=sum(r.get('status') == 'SUCCESS' for r in receipts),
            accepted=sum(r.get('accepted', 0) for r in receipts),
            quarantined=sum(r.get('quarantined', 0) for r in receipts),
            last_error=scope_error or ledger.get('last_error'),
            recovery='OWNER_REVIEW_REQUIRED' if scope_error or uncertain or unresolved or http_error else
                     'REUSED_COMPLETE' if ledger.get('status') == 'COMPLETE' else
                     'EXPIRED_CHECKPOINT_OWNER_REVIEW_REQUIRED' if day != str(now.date()) else
                     'OWNER_AUTHORIZATION_REQUIRED' if not authorized else
                     'RESUME_SAVED_RESPONSE' if saved else 'TICK_WITHIN_APPROVED_WINDOW'))
    return dict(status='AUTHORIZED' if authorized else 'OWNER_AUTHORIZATION_REQUIRED',
        within_approved_dates=plan['first_day'] <= str(now.date()) <= plan['last_day'],
        first_day=plan['first_day'], last_day=plan['last_day'], batches=batches,
        freshness='OBSERVED' if latest else 'UNKNOWN' if accepted else 'MISSING',
        latest_valid_observation_at=latest.isoformat() if latest else None,
        observation_age_seconds=(now-latest).total_seconds() if latest else None,
        freshness_basis='Latest validated stock source timestamp; not universe coverage or session completion',
        incomplete_batches=sum(b['status'] != 'COMPLETE' for b in batches))


def tick(now=None, *, output=OUT, runner=execute, readiness=None):
    # Serialize manifest publication as well as execution. The updater's own lock
    # alone is too late: dump() uses a shared .pending filename.
    if not (output/'OWNER_GRANT.json').exists():
        return dict(status='OWNER_AUTHORIZATION_REQUIRED', requests_sent=0)
    with (output/'.schedule.lock').open('a+b') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return dict(status='ACQUISITION_BUSY', requests_sent=0)
        return _tick(now, output=output, runner=runner, readiness=readiness)


def _tick(now=None, *, output=OUT, runner=execute, readiness=None):
    now=now or datetime.now(IST)
    if now.tzinfo is None:raise ValueError('aware checkpoint required')
    now=now.astimezone(IST)
    plan_path=output/'PROPOSED_MANIFEST.json'
    grant_path=output/'OWNER_GRANT.json'
    # Check owner grant before credentials, archive or provider access.
    if not grant_path.exists():return dict(status='OWNER_AUTHORIZATION_REQUIRED',requests_sent=0)
    raw=plan_path.read_bytes();plan=json.loads(raw);digest=sha(raw)
    grant=json.loads(grant_path.read_text())
    if grant.get('manifest_sha256')!=digest or grant.get('max_requests')!=plan['max_requests']:
        raise PermissionError('STOP_RECURRING_GRANT_SCOPE_MISMATCH')
    day=str(now.date())
    if not plan['first_day']<=day<=plan['last_day']:return dict(status='OUTSIDE_APPROVED_DATES',requests_sent=0)
    session=day_record(day)
    if session['status']!='REGULAR_SCHEDULED':return dict(status='NONREGULAR_SESSION_NO_FETCH',requests_sent=0)
    if readiness is None:
        from ops.host.historical_candle_lifecycle import status
        current=status()
        if current['status']=='RUNNING' or current.get('campaign',{}).get('phase')!='FINISHED':
            return dict(status='DEFERRED_UNTIL_HISTORICAL_CAMPAIGN_FINISHES',requests_sent=0)
        handoff=ROOT/'artifacts/technical-fix-v2/COMPLETION_WORKER.json'
        if not handoff.exists() or json.loads(handoff.read_text()).get('state')!='FINISHED':
            return dict(status='DEFERRED_UNTIL_HISTORICAL_VALIDATION_FINISHES',requests_sent=0)
        state=json.loads((ROOT/'artifacts/historical-candle-archive-v1/hcd02/full-execution.json').read_text())
        last=max(state.get('provider_attempt_times',[]) or [0])
        supplement=ROOT/'artifacts/historical-candle-archive-v1/hcd02/special-full-execution.json'
        if supplement.exists():
            last=max(last,max(json.loads(supplement.read_text()).get('provider_attempt_times',[]) or [0]))
        if now.timestamp()-last<1800:return dict(status='PROVIDER_QUOTA_COOLDOWN',requests_sent=0)
    elif not readiness():return dict(status='ACQUISITION_BUSY',requests_sent=0)
    results=[]
    new_batches=0
    for at in TIMES:
        if at>now.strftime('%H:%M'):continue
        batch=[q for q in plan['requests'] if q['day']==day and q['checkpoint']==at]
        if not batch:continue
        folder=output/'batches'/(day+'T'+at.replace(':',''))
        manifest=folder/'manifest.json'
        saved=folder/'UPDATE_LEDGER.json'
        batch_plan = dict(requests=batch, parent_manifest_sha256=digest)
        if manifest.exists():
            if json.loads(manifest.read_text()) != batch_plan:
                raise RuntimeError('STOP_SAVED_BATCH_SCOPE_CHANGED')
        else:
            if saved.exists():
                raise RuntimeError('STOP_SAVED_BATCH_MANIFEST_MISSING')
            dump(manifest, batch_plan)
        expected=sha(manifest.read_bytes())
        if saved.exists():
            ledger = json.loads(saved.read_text())
            if ledger.get('manifest_sha256') != expected:
                raise RuntimeError('STOP_LEDGER_SCOPE_CHANGED')
            if ledger.get('status') == 'COMPLETE':
                results.append(dict(checkpoint=at,status='REUSED_COMPLETE'))
                continue
        def guarded_fetch(q,token):
            actual=datetime.now(IST)
            if str(actual.date())!=q['day'] or actual.strftime('%H:%M')<q['checkpoint']:
                raise RuntimeError('STOP_CHECKPOINT_EXPIRED_OR_NOT_DUE')
            return get(q,token)
        try:
            result=runner(authorize=expected,fetch=guarded_fetch,manifest_path=manifest,output=folder,expected_digest=expected,pace=2)
        except (BlockingIOError,RuntimeError) as error:
            if 'WRITER_BUSY' not in str(error) and not isinstance(error,BlockingIOError):raise
            return dict(status='ACQUISITION_BUSY',completed_checkpoints=results)
        results.append(dict(checkpoint=at,status=result['status']))
        new_batches+=1
    return dict(status='DUE_CHECKPOINTS_PROCESSED',checkpoints=results,new_batches=new_batches,authorized=True)


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','tick','status']);args=p.parse_args()
    result=prepare() if args.action=='prepare' else status() if args.action=='status' else tick()
    if args.action=='tick' and result.get('new_batches',0):
        from .integrate import main
        main(end=str(datetime.now(IST).date()))
    print(json.dumps(result,indent=2))
