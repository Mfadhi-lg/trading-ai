"""Lightweight read-only operational status from durable execution receipts.

Does not open production SQLite or contact sources. For a fresh guarded queue
census use the existing lifecycle status --json command.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'ops/activation')]
import automatic_acquisition as scheduler
from phase4_schedule import settings,authorization_state


def records(path):
    if not path.is_file():return
    for line in path.read_text().splitlines():
        try:record=json.loads(line)
        except ValueError:continue
        if isinstance(record,dict):yield record


def status(state=None):
    state=Path(state or scheduler.STATE)
    stages={};financial=None;download=None;processing=None;recent_errors=[];productive=[]
    for folder in sorted(state.glob('20??-??-??-*')):
        if not (folder/'admission.json').exists():continue
        admission=json.loads((folder/'admission.json').read_text())
        outcome=json.loads((folder/'outcome.json').read_text()) if (folder/'outcome.json').exists() else {}
        stage=admission.get('exchange','unknown')
        attempted=admission.get('started_at_utc')
        item={'last_attempt':attempted,'state':outcome.get('status','INTERRUPTED_OR_ACTIVE'),
              'finished_at_utc':outcome.get('finished_at_utc'),'receipt':str(folder)}
        if stage not in stages or str(attempted)>str(stages[stage].get('last_attempt')):stages[stage]=item
        if outcome.get('status') in ('STOP','FAILED_JOB','DEFERRED_ENVIRONMENT','EXPIRED_AUTHORIZATION','BLOCKED_SOURCE_POLICY'):
            recent_errors.append({**item,'stage':stage,'error':outcome.get('error') or outcome.get('reason')})
        for row in records(folder/'runtime.log'):
            if row.get('stage')=='financial_batch_outcome':
                count=row.get('result',{}).get('summary',{}).get('outcome_counts',{}).get('accepted',0)
                if count:
                    event={'at':outcome.get('finished_at_utc'),'accepted_jobs':count,'receipt':str(folder)}
                    if financial is None or str(event['at'])>str(financial['at']):financial=event
                    productive.append({'at':event['at'],'kind':'financial','jobs':count})
            if row.get('stage')=='document_stage_outcome':
                result=row.get('result',{});at=row.get('finished_at_utc')
                count=result.get('download',{}).get('outcome_counts',{}).get('validated',0)
                # Dedicated ratings expose their own validated count.
                if row.get('document_stage')=='DOCUMENT_RATING':count=sum(result.get('outcome_counts',{}).get(key,0) for key in ('DIRECT_REPORT_VALIDATED','LANDING_PAGE_RESOLVED','TEMPORARY_OBJECT_REPORT_VALIDATED','HTML_REPORT_VALIDATED'))
                if count:
                    event={'at':at,'validated_jobs':count,'receipt':str(folder)}
                    if download is None or str(at)>str(download['at']):download=event
                    productive.append({'at':at,'kind':'download','jobs':count})
                count=sum(r.get('extraction_status') in ('ok','partial') for r in result.get('outcomes',[])) if isinstance(result.get('outcomes',[]),list) else 0
                if count:
                    event={'at':at,'processed_jobs':count,'receipt':str(folder)}
                    if processing is None or str(at)>str(processing['at']):processing=event
                    productive.append({'at':at,'kind':'processing','jobs':count})
    now=datetime.now(timezone.utc);c=settings()
    def rate(kind):
        events=[r for r in productive if r['kind']==kind and r['at'] and
            0<=(now-datetime.fromisoformat(r['at'])).total_seconds()<=86400]
        return {'successful_jobs_last_24h':sum(r['jobs'] for r in events),
                'successful_jobs_per_hour_over_24h':sum(r['jobs'] for r in events)/24}
    for name in stages:
        marker=state/(name+'.stopped.json')
        if marker.exists():stages[name]['state']='CRITICAL_STOP'
        elif name.startswith('DOCUMENT_') and authorization_state(c,now)!='RUNNING':
            stages[name]['state']=authorization_state(c,now)
    return {'observed_at_utc':now.isoformat(),'source_requests':0,'database_opened':False,
        'admissions':'PAUSED' if (state/'lifecycle-paused.json').exists() else 'ENABLED',
        'authorization':authorization_state(c,now),'authorization_mode':c.get('authorization_mode','finite'),
        'verification_deadline_utc':c['expires_at_utc'],'stages':stages,
        'last_real_financial_acceptance':financial,'last_retained_validated_download':download,
        'last_real_deterministic_processing':processing,
        'rates':{kind:rate(kind) for kind in ('financial','download','processing')},
        'recent_failures':sorted(recent_errors,key=lambda r:str(r['last_attempt']))[-10:],
        'queue_and_cooldown_census_command':'python3 -B ops/host/run_acquisition_lifecycle.py status --json',
        'health':'DEGRADED' if any(r['state']=='CRITICAL_STOP' for r in stages.values()) else
            'PAUSED' if (state/'lifecycle-paused.json').exists() else 'NO_RECENT_DOWNLOADS' if rate('download')['successful_jobs_last_24h']==0 else 'PRODUCTIVE',
        'limitation':'Retained download validation receipts are not a fresh archive integrity census; installed scheduler and storage require guarded live status'}


if __name__=='__main__':print(json.dumps(status(),indent=2))
