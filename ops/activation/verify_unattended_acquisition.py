"""Read-only audit of real scheduled evidence; never requests or retries sources."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / 'artifacts/automatic-acquisition'
RECEIPTS = ROOT / 'artifacts/bse-browser-results'


def read_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def audit_existing_runs(state=STATE, receipts=RECEIPTS):
    first = {'NSE': None, 'BSE': None}
    scheduled = []
    for out in sorted(state.glob('20??-??-??-*')):
        match = re.fullmatch(r'(\d{4}-\d{2}-\d{2})-(nse-primary|bse-morning|bse-evening)', out.name)
        if not match or not out.is_dir():
            continue
        admission = read_json(out / 'admission.json')
        outcome = read_json(out / 'outcome.json')
        if not admission:
            continue
        exchange = admission['exchange']
        item = {'exchange':exchange,'job':admission['job'],'scheduled_at_ist':admission['scheduled_at_ist'],
            'started_at_utc':admission['started_at_utc'],'path':str(out),
            'dispatch_origin':admission.get('dispatch_origin','unverified'),
            'status':outcome['status'] if outcome else 'RUNNING_OR_INTERRUPTED',
            'automatic_retry':False,'source_attempt_confirmed':False}
        if outcome and outcome['status'] == 'SKIPPED_ALREADY_ATTEMPTED':
            item['source_status'] = 'SKIPPED_EXISTING_RECEIPT_NO_NEW_ATTEMPT'
        elif exchange == 'NSE':
            records = []
            try:
                lines = (out/'runtime.log').read_text().splitlines()
            except OSError:
                lines = []
            for line in lines:
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record,dict):
                    records.append(record)
            item['source_attempt_confirmed'] = any(r.get('stage') in
                ('source_request_started','preview','outcome') for r in records)
            item['source_request_count'] = sum(r.get('stage')=='source_request_started' for r in records)
            item['responses'] = [r for r in records if r.get('stage')=='source_response_received']
            item['summary'] = read_json(out/'acquisition-summary.json')
            item['source_status'] = 'OBSERVED' if item['source_attempt_confirmed'] else 'NO_SOURCE_REQUEST_EVIDENCE'
        else:
            slot = 'morning' if admission['job']=='bse-morning' else 'evening'
            receipt_path = receipts/(match[1]+'-'+slot+'.json')
            receipt = read_json(receipt_path)
            if receipt:
                item['receipt_path'] = str(receipt_path)
                item['receipt_status'] = receipt.get('status')
                item['source_attempt_confirmed'] = 'response' in receipt
                item['source_status'] = 'OBSERVED' if item['source_attempt_confirmed'] else 'NO_RESPONSE_EVIDENCE'
                item['response'] = receipt.get('response')
                raw = receipts/(match[1]+'-'+slot+'.raw')
                verification = read_json(out/'verification.json')
                if raw.exists() and verification:
                    digest = hashlib.sha256(raw.read_bytes()).hexdigest()
                    item['source_hash_verified'] = digest == verification.get('raw_sha256')
                    item['source_sha256'] = digest
                item['summary'] = read_json(out/'acquisition-summary.json')
            else:
                item['source_status'] = 'NO_BROWSER_RECEIPT'
        item['outcome_recorded'] = outcome is not None
        try:
            delay=(datetime.fromisoformat(item['started_at_utc'])-
                   datetime.fromisoformat(item['scheduled_at_ist'])).total_seconds()
            timing_ok=0 <= delay < 60
        except (ValueError,TypeError):
            timing_ok=False
        item['scheduled_timing_verified']=timing_ok
        if outcome and item['source_attempt_confirmed']:
            if outcome['status']=='STOP':
                item['verification_status']='FAILURE_OBSERVED'
            else:
                summary=item.get('summary') or {}
                budgets_ok=(bool(summary.get('periods')) and summary.get('source_rows',6001)<=6000 and
                    sum(row.get('queue_decisions',{}).get('queued',0) for row in summary.get('periods',[]))<=100)
                if exchange=='NSE':
                    source_ok=(item.get('source_request_count')==2 and len(item.get('responses',[]))==2 and
                        all(row.get('status_code')==200 and row.get('fetch_status')=='ok' for row in item['responses']))
                else:
                    source_ok=(item.get('source_hash_verified') is True and
                        (item.get('response') or {}).get('status_code')==200 and
                        (item.get('response') or {}).get('auth_mode')=='public_browser')
                item['verification_status']='PASS' if timing_ok and source_ok and budgets_ok else 'EVIDENCE_INCOMPLETE'
        scheduled.append(item)
    scheduled.sort(key=lambda item: datetime.fromisoformat(item['started_at_utc']))
    for item in scheduled:
        exchange = item['exchange']
        if (first[exchange] is None and item['source_attempt_confirmed'] and
                item['outcome_recorded'] and item['dispatch_origin']=='launchd'):
            first[exchange]=item
    report = {'observed_at_utc':datetime.now(timezone.utc).isoformat(),
        'mode':'read_only_existing_logs_and_receipts','source_requests':0,'forced_attempts':0,
        'first_real_unattended_attempts':{name:row or {'status':'PENDING_SLOT_OR_SOURCE_EVIDENCE'}
                                       for name,row in first.items()},
        'scheduled_outcomes':scheduled,'new_workers_started':False}
    state.mkdir(parents=True,exist_ok=True)
    (state/'first-unattended-verification.json').write_text(json.dumps(report,indent=2)+'\n')
    step3=[]
    for out in sorted(state.glob('20??-??-??-*')):
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}-(current-financial|market-cap)-\d{4}',out.name):
            continue
        admission=read_json(out/'admission.json')
        if admission:
            step3.append({'path':str(out),'admission':admission,'outcome':read_json(out/'outcome.json'),
                'summary':read_json(out/'acquisition-summary.json')})
    (state/'step3-unattended-verification.json').write_text(json.dumps({
        'mode':'read_only_existing_logs','source_requests':0,'runs':step3},indent=2)+'\n')
    return report


if __name__=='__main__':
    print(json.dumps(audit_existing_runs(),indent=2))
