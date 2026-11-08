"""Run one bounded financial/download verification while admissions stay paused."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'ops/activation'), str(ROOT/'ops/host'), str(ROOT)]
import acquisition_lifecycle as lifecycle
import automatic_acquisition as dispatcher
from step3_schedule import execute_once


def main():
    evidence = ROOT/'artifacts/scraper-capacity-repair-20261102'
    out = evidence/'live-canary'
    with lifecycle.lock(lifecycle.STATE/'operator.lock'), lifecycle.lock(
            lifecycle.ACTIVATION/'.scheduler-admission.lock'):
        if not lifecycle.PAUSE.exists():
            raise RuntimeError('Repair verification requires paused admissions')
        dispatcher.verify_config()
        out.mkdir(exist_ok=False)
        dispatcher.save(out/'admission.json', {
            'job': 'capacity-repair-canary', 'exchange': 'FINANCIAL',
            'scheduled_at_ist': datetime.now(dispatcher.IST).isoformat(),
            'dispatch_origin': 'operator_repair', 'automatic_retry': False,
            'authorization': 'User requested repair and verification of blocked scraping'})
        code = execute_once('FINANCIAL', out, paired_downloads=True)
        summary = dispatcher.summarize_runtime(out)
        dispatcher.save(out/'acquisition-summary.json', summary)
        downloads = [r['result']['result'].get('download', {})
                     for r in summary['periods'] if r['stage'] == 'document_stage_outcome']
        validated = sum(r.get('outcome_counts', {}).get('validated', 0) for r in downloads)
        result = {'status': 'PASS' if code == 0 and validated else 'BLOCKED',
                  'exit_code': code, 'validated_downloads': validated,
                  'finished_at_utc': datetime.now(timezone.utc).isoformat(),
                  'admissions_paused': True, 'automatic_retry': False}
        dispatcher.save(out/'outcome.json', result)
        print(json.dumps(result), flush=True)
        return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
