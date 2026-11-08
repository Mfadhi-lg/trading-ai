"""Explicit bounded recovery operation using current claims and shared budgets."""
import argparse
from datetime import datetime
import fcntl
import json
from pathlib import Path
import sys

HERE=Path(__file__).resolve().parent
sys.path[:0]=[str(HERE),str(HERE.parents[1])]
import automatic_acquisition as scheduler
from scrapper.sources.exchange.reporting_periods import IST


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--document-stage',choices=('discovery','download','processing','rating','patrol'))
    parser.add_argument('--execute',action='store_true')
    parser.add_argument('--local-historical-processing',action='store_true',
        help='One-document networkless local campaign; daily extraction capacity is shared')
    parser.add_argument('--resume-local-campaign',action='store_true',help='Resume only the exact retained work identity')
    args=parser.parse_args()
    if not args.execute:
        print(json.dumps({'status':'READ_ONLY','message':'--execute is required; no work dispatched'}))
        return 0
    if not args.document_stage:raise ValueError('Select one document stage')
    if args.local_historical_processing and args.document_stage!='processing':
        raise ValueError('Local historical mode is processing only')
    if args.resume_local_campaign and not args.local_historical_processing:raise ValueError('Local campaign required')
    backup=scheduler.ROOT/'artifacts/acquisition-recovery-20261026/raw/backup.json'
    receipt=json.loads(backup.read_text())
    if receipt.get('status')!='PASS' or receipt.get('quick_check')!='ok':
        raise RuntimeError('Recovery backup verification required')
    with (HERE/'.scheduler-admission.lock').open('rb') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        scheduler.verify_config()
        # Actual current time, manual origin, durable queue identities. No clock replay.
        execute=None
        if args.local_historical_processing:
            from phase4_schedule import execute_once
            execute=lambda kind,out:execute_once(kind,out,local_historical_processing=True,resume_local_campaign=args.resume_local_campaign)
        result=scheduler.run_slot('document-'+args.document_stage,
            'DOCUMENT_'+args.document_stage.upper(),datetime.now(IST),execute=execute)
    print(json.dumps(result,default=str))
    return 2 if result['status']=='STOP' else 0


if __name__=='__main__':sys.exit(main())
