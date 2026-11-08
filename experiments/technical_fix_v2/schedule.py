"""Bounded recurring proposal and explicit grant gate. Never installs a timer."""
from datetime import datetime, timedelta
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


def tick(now=None, *, output=OUT, runner=execute, readiness=None):
    now=now or datetime.now(IST)
    if now.tzinfo is None:raise ValueError('aware checkpoint required')
    now=now.astimezone(IST)
    plan_path=output/'PROPOSED_MANIFEST.json'
    grant_path=output/'OWNER_GRANT.json'
    # Check owner grant before credentials, archive or provider access.
    if not grant_path.exists():return dict(status='OWNER_AUTHORIZATION_REQUIRED',requests_sent=0)
    plan=json.loads(plan_path.read_text());digest=sha(plan_path.read_bytes())
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
        if saved.exists() and json.loads(saved.read_text()).get('status')=='COMPLETE':
            results.append(dict(checkpoint=at,status='REUSED_COMPLETE'))
            continue
        dump(manifest,dict(requests=batch,parent_manifest_sha256=digest))
        expected=sha(manifest.read_bytes())
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
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','tick']);args=p.parse_args()
    result=prepare() if args.action=='prepare' else tick()
    if args.action=='tick' and result.get('new_batches',0):
        from .integrate import main
        main(end=str(datetime.now(IST).date()))
    print(json.dumps(result,indent=2))
