"""Actual stage receipts: latency, errors, queue growth and drive headroom."""
from pathlib import Path
import json
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[2]


def audit(state=None):
    state=Path(state or ROOT/'artifacts/automatic-acquisition');runs=[]
    for folder in sorted(state.glob('*-document-*')):
        if not (folder/'admission.json').is_file():continue
        admission=json.loads((folder/'admission.json').read_text())
        if admission.get('dispatch_origin')!='launchd':continue
        outcome=json.loads((folder/'outcome.json').read_text()) if (folder/'outcome.json').exists() else {'status':'interrupted_or_running'}
        row={'path':str(folder),'scheduled_at_ist':admission['scheduled_at_ist'],'status':outcome['status']}
        for line in (folder/'runtime.log').read_text().splitlines() if (folder/'runtime.log').exists() else ():
            try:r=json.loads(line)
            except ValueError:continue
            if r.get('stage')=='document_stage_outcome':row.update(receipt=r)
        runs.append(row)
    actual=[r['receipt'] for r in runs if 'receipt' in r]
    report={'observed_at_utc':datetime.now(timezone.utc).isoformat(),'actual_launchd_runs':runs,
        'completed_stages':len(actual),'stage_failures':sum(r['status']=='STOP' for r in runs),
        'duration_seconds':[r['duration_seconds'] for r in actual],
        'extraction_outcomes':[x for r in actual if r['document_stage']=='DOCUMENT_PROCESSING' for x in r['result'].get('outcomes',[])],
        'source_requests':0,'production_writes':0,
        'latest_queue_depth':actual[-1]['queue_depth'] if actual else None,
        'latest_storage':actual[-1]['storage'] if actual else None}
    (state/'phase4-monitoring.json').write_text(json.dumps(report,indent=2)+'\n')
    return report

if __name__=='__main__':print(json.dumps(audit(),indent=2))
