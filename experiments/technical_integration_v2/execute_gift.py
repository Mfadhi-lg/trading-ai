"""Execute only the separately approved four GIFT quotation requests once."""
from datetime import date
import gzip
import hashlib
import json
import subprocess
import time

from .prepare_narrowed import ROOT, OUT, BASE, sha
from .execute_narrowed import now, save, strict_payload, classify_error
from experiments.earnings_reaction_v1.retrieve_er03 import DailyRequest
from experiments.earnings_reaction_v1.upstox_candles import url_for

APPROVED_GIFT = '64a0b961a8eb29015026844d4de1ee895137fe29abff7c46e5ea24c088fffef8'
EXTRA = OUT / 'additional'


def execute():
    manifest = EXTRA / 'PENDING_GIFT_MANIFEST.json'
    if sha(manifest) != APPROVED_GIFT:
        raise PermissionError('GIFT manifest hash mismatch')
    plan = json.loads(manifest.read_text())
    assert [r['number'] for r in plan['requests']] == [4, 15, 16, 17]
    original = json.loads((BASE / 'UPSTOX_DIAGNOSTIC_MANIFEST.json').read_text())
    master = []
    for name, digest in plan['metadata'].items():
        assert sha(ROOT / name) == digest
        master.extend(json.loads(gzip.decompress((ROOT / name).read_bytes())))
    for r in plan['requests']:
        assert r == next(v for v in original['requests'] if v['number'] == r['number'])
        assert [v for v in master if v['instrument_key'] == r['instrument_key']] == [r['identity']]
        q = DailyRequest(r['instrument_key'], r['unit'], r['interval'], date.fromisoformat(r['from_date']), date.fromisoformat(r['to_date']))
        assert url_for(q) == r['url']
    previous = json.loads((OUT / 'EXECUTION_LEDGER.json').read_text())
    assert previous['complete'] and previous['attempted_count'] == 16
    frozen = [OUT / 'EXECUTION_LEDGER.json', OUT / 'UPSTOX_REDUCED_MANIFEST.json',
              OUT / 'OWNER_AUTHORIZATION.json', manifest, BASE / 'UPSTOX_DIAGNOSTIC_MANIFEST.json']
    frozen.extend(ROOT / r['retained_path'] for r in previous['requests'])
    public = EXTRA / 'PUBLIC_TREASURY_LEDGER.json'
    frozen.append(public)
    frozen.extend(ROOT / r['retained_path'] for r in json.loads(public.read_text())['requests'])
    ledger = EXTRA / 'GIFT_EXECUTION_LEDGER.json'
    state = dict(manifest_sha256=APPROVED_GIFT,
        owner_approval_reference='Owner reply "i authorise" to the explicit four-request GIFT quotation manifest approval question.',
        approved_request_count=4, started_at=now(), automatic_retries=0, requests=[], complete=False, global_stop=None)
    with ledger.open('x') as stream:
        json.dump(state, stream, indent=2)
    save(EXTRA / 'GIFT_PREEXECUTION_BASELINE.json', {str(p.relative_to(ROOT)): sha(p) for p in frozen})
    save(EXTRA / 'GIFT_OWNER_AUTHORIZATION.json', dict(manifest_sha256=APPROVED_GIFT,
        approval=state['owner_approval_reference'], recorded_at=now(), approved_request_numbers=[4, 15, 16, 17],
        price_identity='GENERIC_GIFT_NIFTY_QUOTATION;NOT_A_VERIFIED_FUTURES_CONTRACT',
        other_authenticated_or_paid_retrievals_authorized=False))
    rawdir = EXTRA / 'gift-raw'
    rawdir.mkdir(exist_ok=True)
    stopped_key = None
    stopped_intervals = {}
    last_finished = None
    try:
        from experiments.earnings_reaction_v1.execute_ta02d import secure_supplier
        token = secure_supplier()
        if not isinstance(token, str) or len(token) < 20 or any(c in token for c in '\r\n"\\'):
            raise ValueError('unsafe credential handoff')
        for r in plan['requests']:
            rec = {k: r[k] for k in ('number', 'series', 'instrument_key', 'unit', 'interval', 'url', 'from_date', 'to_date')}
            rec['attempted'] = False
            state['requests'].append(rec)
            reason = state['global_stop'] or stopped_key or stopped_intervals.get(r['unit'])
            if reason:
                rec.update(status='SKIPPED', reason=reason)
                save(ledger, state)
                continue
            if last_finished is not None:
                time.sleep(max(0, 1.05 - (time.monotonic() - last_finished)))
            rec.update(attempted=True, attempted_at=now(), status='ATTEMPT_STARTED')
            save(ledger, state)
            config = 'header = "Accept: application/json"\nheader = "Authorization: Bearer ' + token + '"\n'
            result = subprocess.run(['/usr/bin/curl', '--disable', '--config', '-', '--silent', '--proto', '=https',
                '--max-redirs', '0', '--retry', '0', '--max-time', '30', '--request', 'GET',
                '--write-out', '\n%{http_code}', r['url']], input=config.encode(),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
            last_finished = time.monotonic()
            rec['received_at'] = now()
            if result.returncode:
                rec.update(status='TRANSPORT_FAILURE', curl_exit_code=result.returncode)
                state['global_stop'] = 'TRANSPORT_FAILURE_NO_RETRY'
                save(ledger, state)
                continue
            raw, status = result.stdout.rsplit(b'\n', 1)
            status = int(status)
            rec.update(http_status=status, response_sha256=hashlib.sha256(raw).hexdigest(), response_bytes=len(raw))
            if token.encode() in raw:
                raise ValueError('unsafe echo')
            try:
                payload = strict_payload(raw)
            except (ValueError, UnicodeError):
                payload = None
            if status != 200 or not isinstance(payload, dict) or payload.get('status') != 'success':
                classification, codes = classify_error(status, payload)
                rec.update(status=classification, provider_error_codes=codes)
                if classification.startswith('SERIES_STOP'):
                    stopped_key = f"request {r['number']}: {classification}"
                elif classification.startswith('INTERVAL_STOP'):
                    stopped_intervals[r['unit']] = f"request {r['number']}: {classification}"
                else:
                    state['global_stop'] = f"request {r['number']}: {classification}"
            else:
                path = rawdir / f"{r['number']:02d}-{rec['response_sha256']}.json"
                with path.open('xb') as stream:
                    stream.write(raw)
                rec['retained_path'] = str(path.relative_to(ROOT))
                data = payload.get('data')
                candles = data.get('candles') if isinstance(data, dict) else None
                if not isinstance(candles, list):
                    rec['status'] = 'GLOBAL_STOP_MALFORMED_CANDLE_SCHEMA'
                    state['global_stop'] = rec['status']
                else:
                    rec.update(status='SUCCESS' if candles else 'EMPTY_WINDOW', returned_bars=len(candles))
            save(ledger, state)
            print(json.dumps({k: rec[k] for k in ('number', 'unit', 'status', 'http_status', 'returned_bars') if k in rec}), flush=True)
        state['complete'] = state['global_stop'] is None
    except Exception as error:
        state['global_stop'] = 'LOCAL_FAILURE_' + type(error).__name__ + ';NO_RETRY;DETAILS_OMITTED'
        recorded = {r['number'] for r in state['requests']}
        state['requests'].extend(dict(number=r['number'], series=r['series'], unit=r['unit'],
            attempted=False, status='SKIPPED', reason=state['global_stop']) for r in plan['requests'] if r['number'] not in recorded)
    state.update(finished_at=now(), attempted_count=sum(r['attempted'] for r in state['requests']),
        skipped_count=sum(not r['attempted'] for r in state['requests']))
    save(ledger, state)
    if not all(sha(ROOT / name) == digest for name, digest in json.loads((EXTRA / 'GIFT_PREEXECUTION_BASELINE.json').read_text()).items()):
        raise ValueError('Previously acquired evidence changed')
    print(json.dumps({k: state[k] for k in ('complete', 'attempted_count', 'skipped_count', 'global_stop')}), flush=True)


if __name__ == '__main__':
    execute()
