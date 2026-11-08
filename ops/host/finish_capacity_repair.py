"""Archive the diagnosed stop only after a successful bounded repair canary."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'ops/activation'), str(ROOT/'ops/host'), str(ROOT)]
import acquisition_lifecycle as lifecycle
import automatic_acquisition as dispatcher


def main():
    evidence = ROOT/'artifacts/scraper-capacity-repair-20261102'
    canary = lifecycle.read(evidence/'live-canary/outcome.json')
    if canary['status'] != 'PASS' or canary['exit_code'] or not canary['validated_downloads']:
        raise RuntimeError('Successful financial/paired-download verification required')
    marker = dispatcher.STATE/'FINANCIAL.stopped.json'
    with lifecycle.lock(lifecycle.STATE/'operator.lock'), lifecycle.lock(
            lifecycle.ACTIVATION/'.scheduler-admission.lock'), lifecycle.lock(
            lifecycle.ACTIVATION/'.nse-owner.lock'):
        if not lifecycle.PAUSE.exists():
            raise RuntimeError('Expected paused admissions during repair')
        dispatcher.verify_config()
        checks = lifecycle.quiescent()
        original = lifecycle.read(marker)
        expected = lifecycle.read(dispatcher.STATE/'2026-11-01-current-financial-1715/outcome.json')
        if original != expected:
            raise RuntimeError('Financial stop changed; preserve for diagnosis')
        marker.replace(evidence/'FINANCIAL.stopped.before.json')
        lifecycle.atomic(evidence/'stop-resolution.json', {
            'status': 'PASS', 'archived_marker': str(evidence/'FINANCIAL.stopped.before.json'),
            'canary': canary, 'checks': checks,
            'at_utc': datetime.now(timezone.utc).isoformat()})
    result = lifecycle.wake()
    lifecycle.atomic(evidence/'resumed.json', result)
    print(json.dumps({'status': result['status'],
                      'admissions_paused': result.get('admissions_paused'),
                      'financial_stop_exists': marker.exists(),
                      'next_batch': result['schedule']['next_scheduled_batch']}), flush=True)


if __name__ == '__main__':
    main()
