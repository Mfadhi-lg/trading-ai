"""Finite read-only observer: receipts/resources now, guarded report after expiry.

No scheduler ticks, source requests, queue changes or activation/resume operations.
The existing dispatcher, permits/cooldowns and STOP latches enforce protections.
"""
import hashlib,json,os,plistlib,subprocess,time
from datetime import datetime,timezone
from pathlib import Path
from phase4_trial_preflight import snapshot_trial
from phase4_schedule import jobs
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
OUT=ROOT/'artifacts/phase4/routine/trial';STATE=ROOT/'artifacts/automatic-acquisition'

def save(name,value):
    p=OUT/name;tmp=p.with_suffix(p.suffix+'.tmp')
    tmp.write_text(json.dumps(value,indent=2,default=str)+'\n');tmp.replace(p)

def read_receipts(epoch):
    rows=[]
    for folder in sorted(STATE.glob('*-document-*')):
        try:
            a=json.loads((folder/'admission.json').read_text())
            if a.get('dispatch_origin')!='launchd' or datetime.fromisoformat(a['started_at_utc'])<epoch:continue
            r={'path':str(folder),'admission':a,'outcome':json.loads((folder/'outcome.json').read_text()) if (folder/'outcome.json').exists() else {'status':'running_or_interrupted'}}
            r['records']=[]
            if (folder/'runtime.log').exists():
                text=(folder/'runtime.log').read_text()
                for line in text.splitlines():
                    try:item=json.loads(line)
                    except ValueError:continue
                    if isinstance(item,dict):r['records'].append(item)
                r['sqlite_contention_evidence']=[line[:800] for line in text.splitlines() if 'database is locked' in line.lower() or 'database is busy' in line.lower()]
            rows.append(r)
        except (OSError,ValueError):continue # Atomic writer may still be publishing receipt.
    return rows

def report(rows,phase='RUNNING',final=None,error=None):
    outcomes=[record for row in rows for record in row['records'] if record.get('stage')=='document_stage_outcome']
    counts={}
    for row in rows:
        k=row['outcome']['status'];counts[k]=counts.get(k,0)+1
    data={'phase':phase,'updated_at_utc':datetime.now(timezone.utc).isoformat(),
        'trial_config':json.loads((HERE/'phase4_schedule.json').read_text()),'actual_launchd_runs':rows,
        'slot_outcomes':counts,'stage_receipts':outcomes,
        'new_document_proof':(final or {}).get('unattended_verification',{'status':'PENDING_UNATTENDED_NEW_DOCUMENT_PROOF'}),
        'monitor_source_requests':0,'monitor_production_writes':0,'final_snapshot_error':error}
    if final:
        data['final_database_snapshot']=final
        baseline=json.loads((OUT/'preflight/snapshot.json').read_text())
        data['table_count_deltas']={t:n-baseline['counts'][t] for t,n in final['counts'].items()}
        data['delta_scope']='Whole database during trial; Phase3 may also contribute. Selected stage lineage provides Phase4 attribution.'
    save('status.json',data)
    discovery=[r for r in outcomes if r['document_stage']=='DOCUMENT_DISCOVERY']
    downloads=[r for r in outcomes if r['document_stage']=='DOCUMENT_DOWNLOAD']
    processing=[r for r in outcomes if r['document_stage']=='DOCUMENT_PROCESSING']
    download_counts={}
    for r in downloads:
        for k,n in r['result'].get('download',{}).get('outcome_counts',{}).items():download_counts[k]=download_counts.get(k,0)+n
    extraction=[x for r in processing for x in r['result'].get('outcomes',[])]
    source_observations=[x for r in discovery for x in r['result'].get('official',{}).get('observations',[])]
    restrictions=[x for x in source_observations if x.get('http_status') in (401,403,429) or x.get('status')=='source_access_blocked']
    text=f'''# Phase 4 Configuration A trial — {phase}

Updated {data['updated_at_utc']}. Activation {data['trial_config']['activated_at_utc']}; expiry {data['trial_config']['expires_at_utc']}.

Actual launchd slots: {len(rows)}; outcomes: {json.dumps(counts)}. Completed discovery stage receipts: {len(discovery)}; download: {len(downloads)}; processing: {len(processing)}. These include idle slots and do not establish document success.

Canonical download outcomes: {json.dumps(download_counts)}. Automatic extraction outcomes: {json.dumps(extraction)}. Official/issuer discovery observations: {json.dumps(source_observations)}. Source restriction observations: {json.dumps(restrictions)}.

New-document lifecycle evidence gate: **{data['new_document_proof']['status']}**. The prior Sacheerome canary is excluded. No claim of unattended end-to-end success follows from a tick or idle receipt.

Detailed stage lineage, discovery failures, request/error evidence, queues and timing are in status.json and original slot directories. Resource observations are in resource-samples.jsonl; they are sampled values, not guaranteed peaks. Final snapshot request/download/fetch/rating rows cover the database interval and can include Phase3; correlate with document stage windows and selected job/content IDs before attributing totals to Phase4. Permit reservations are not proven dispatched HTTP requests. Unknown/unrecorded HTTP outcomes remain unknown.

Existing per-source restrictions, denial cooldowns and circuits stop affected requests; existing stage STOP latches stop genuine failures. This observer never resumes work, bypasses holds, replays slots or starts a production worker. Phase3 schedules and historical document capacity remain unchanged. CPU/memory samples and contention logs support later sizing; idle trials or sparse native PDFs do not justify increasing source request budgets or establish OCR throughput.
'''
    if final:
        text+='\nDatabase table count deltas (whole trial, not solely Phase4): '+json.dumps(data['table_count_deltas'])+'\n'
        text+='\nNew download attempts: '+json.dumps(final['new_download_attempts'])+'\n'
        text+='\nFinal source cooldown/circuit state: '+json.dumps(final['host_state'])+'\n'
        text+='\nRemaining Phase4 blocker: '+('validate source/queue/resource outcomes before marking operational acceptance complete.' if data['new_document_proof']['status']=='PASS' else 'a genuinely new eligible document completing the lifecycle unattended has not been established.')+'\n'
    if error:text+='\nFinal guarded database inspection remains unavailable: '+error+'. Retain receipts; no automatic repair or safety override.\n'
    (OUT/'REPORT.md').write_text(text)
    return data

def resources():
    sample={'observed_at_utc':datetime.now(timezone.utc).isoformat()}
    r=subprocess.run(['docker','stats','--no-stream','--format','{{json .}}','trading-ai-phase4-routine'],capture_output=True,text=True,timeout=15)
    if r.returncode==0 and r.stdout.strip():
        sample['container_samples']=[json.loads(x) for x in r.stdout.splitlines()]
    else:sample['container']='not_running_or_unavailable'
    info=subprocess.run(['diskutil','info','-plist','/Volumes/My Passport'],capture_output=True,timeout=15)
    if info.returncode==0:
        p=plistlib.loads(info.stdout);sample['volume_uuid']=p.get('VolumeUUID');sample['physical_free_bytes']=p.get('APFSContainerFree')
    with (OUT/'resource-samples.jsonl').open('a') as f:f.write(json.dumps(sample)+'\n')

def main():
    config=json.loads((HERE/'phase4_schedule.json').read_text())
    assert config['enabled'] and config['historical_limit']==0 and config['concurrency']==1
    epoch=datetime.fromisoformat(config['activated_at_utc']);end=datetime.fromisoformat(config['expires_at_utc'])
    save('observer-started.json',{'pid':os.getpid(),'started_at_utc':datetime.now(timezone.utc).isoformat(),'expires_at_utc':end.isoformat(),'source_requests':0,'production_writes':0})
    while datetime.now(timezone.utc)<end:
        try:
            report(read_receipts(epoch));resources()
        except Exception as exc:
            save('observer-error.json',{'at':datetime.now(timezone.utc).isoformat(),'error':str(exc),'automatic_repair':False})
        time.sleep(min(60,max(0,(end-datetime.now(timezone.utc)).total_seconds())))
    assert jobs(datetime.now(timezone.utc))==[],'Expired Phase4 still admits jobs'
    save('expiry-verified.json',{'at':datetime.now(timezone.utc).isoformat(),'phase4_jobs':[],'in_flight_policy':'bounded completion; no state deletion','historical_limit':0})
    final=None;error=None
    # Wait for a free existing owner; never interfere with Phase3/in-flight work.
    for attempt in range(15):
        try:
            final=snapshot_trial(OUT/f'final-inspection-{attempt:02}',final=True);break
        except Exception as exc:error=str(exc);time.sleep(60)
    report(read_receipts(epoch),phase='EXPIRED',final=final,error=error if final is None else None)
    save('observer-finished.json',{'finished_at_utc':datetime.now(timezone.utc).isoformat(),'report':str(OUT/'REPORT.md'),'final_snapshot_passed':final is not None})

if __name__=='__main__':main()
